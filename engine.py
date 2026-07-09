import tempfile
import time
from pathlib import Path

from google import genai
from google.genai import types

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    PdfReader = None
    PdfWriter = None

_DEFAULT_MODEL = "gemini-3.1-flash-lite"
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tiff", ".tif"}

# Gemini 3.1 Flash-Lite heeft een output-limiet van ~64K tokens. Een dicht,
# officieel document produceert al snel 800-1500 output-tokens per pagina bij
# vertaling, dus een groot PDF-bestand moet in stukken gesplitst worden om
# zowel de input- als output-limieten niet te overschrijden.
PAGES_PER_CHUNK = 20

GEMINI_SYSTEM_TEMPLATE = """You are a professional translator (legal, official, and documentary use).

**Output language: {target_language} only**

- Translate all input text into {target_language}.
- Write only in {target_language}. 
- Do not leave long spans of the original language in the output.
- Never start the output with meta phrases (e.g., "De vertaling:", "The translation is:").
- No preambles or notes.
- Stay faithful: do not invent facts, names, dates, or places. Preserve numbers and dates exactly.
- Structure the text with normal line breaks, top-to-bottom, following the document's layout flow.
- Translate the full source content; do not omit sentences.
- Never include UI elements in the trsnalation output (e.g., "Click here", "Select a file", "Submit", "Cancel")."""

USER_PROMPT = "Read every word on the uploaded document (including stamps, handwritten text, and notes) and translate it fully into plain {target_language} text according to the system rules."


class GeminiTranslator:
    def __init__(self, api_key: str, model_name: str = None):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name or _DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Publieke methode: vertaalt één bronbestand (evt. in meerdere delen)
    # ------------------------------------------------------------------
    def translate_single_file(self, src_path: Path, dest_path: Path, target_language: str = "Nederlands", log_callback=None) -> bool:
        """Uploadt, vertaalt (evt. in delen) en verwijdert een bestand via Gemini."""
        def _log(msg):
            if log_callback:
                log_callback(msg)

        if src_path.suffix.lower() == ".pdf":
            page_count = self._get_pdf_page_count(src_path, _log)
            if page_count and page_count > PAGES_PER_CHUNK:
                return self._translate_large_pdf(src_path, dest_path, target_language, page_count, _log)

        # Klein bestand (of geen PDF): gewoon in één keer verwerken.
        translated_text = self._translate_chunk(src_path, target_language, _log)
        if translated_text is None:
            return False

        dest_path.write_text(translated_text, encoding="utf-8")
        _log(f"   ✅ Opgeslagen als: {dest_path.name}")
        return True

    # ------------------------------------------------------------------
    # Grote PDF's: splitsen in stukken van PAGES_PER_CHUNK pagina's
    # ------------------------------------------------------------------
    def _get_pdf_page_count(self, src_path: Path, _log) -> int | None:
        if PdfReader is None:
            _log("   ⚠️ 'pypdf' is niet geïnstalleerd — kan grote PDF's niet automatisch opsplitsen.")
            _log("      Installeer met: pip install pypdf")
            return None
        try:
            reader = PdfReader(str(src_path))
            return len(reader.pages)
        except Exception as e:
            _log(f"   ⚠️ Kon paginatelling niet bepalen ({e}), probeer bestand in één keer te verwerken.")
            return None

    def _translate_large_pdf(self, src_path: Path, dest_path: Path, target_language: str, page_count: int, _log) -> bool:
        total_chunks = (page_count + PAGES_PER_CHUNK - 1) // PAGES_PER_CHUNK
        _log(f"   -> Groot document gedetecteerd ({page_count} pagina's). "
             f"Wordt in {total_chunks} delen van max. {PAGES_PER_CHUNK} pagina's verwerkt.")

        reader = PdfReader(str(src_path))
        translated_parts = []

        with tempfile.TemporaryDirectory(prefix="translator_chunks_") as tmp_dir:
            tmp_dir_path = Path(tmp_dir)

            for chunk_idx in range(total_chunks):
                start = chunk_idx * PAGES_PER_CHUNK
                end = min(start + PAGES_PER_CHUNK, page_count)

                _log(f"\n   [Deel {chunk_idx + 1}/{total_chunks}] Pagina's {start + 1}-{end}")

                writer = PdfWriter()
                for page_num in range(start, end):
                    writer.add_page(reader.pages[page_num])

                chunk_path = tmp_dir_path / f"{src_path.stem}_part{chunk_idx + 1}.pdf"
                with open(chunk_path, "wb") as f:
                    writer.write(f)

                chunk_text = self._translate_chunk(chunk_path, target_language, _log)
                if chunk_text is None:
                    _log(f"   ❌ Deel {chunk_idx + 1}/{total_chunks} is mislukt. Vertaling wordt afgebroken.")
                    return False

                translated_parts.append(chunk_text)
                time.sleep(0.5)

        full_text = "\n\n".join(translated_parts)
        dest_path.write_text(full_text, encoding="utf-8")
        _log(f"\n   ✅ Alle {total_chunks} delen samengevoegd en opgeslagen als: {dest_path.name}")
        return True

    # ------------------------------------------------------------------
    # Eén bestand (of PDF-deel) uploaden en laten vertalen
    # ------------------------------------------------------------------
    def _translate_chunk(self, src_path: Path, target_language: str, _log) -> str | None:
        uploaded_file = None
        try:
            mime_type = "application/pdf" if src_path.suffix.lower() == ".pdf" else None

            _log(f"   -> Uploaden naar Vertaal-Engine ({src_path.name})")
            upload_kwargs = {"file": src_path}
            if mime_type:
                upload_kwargs["config"] = types.UploadFileConfig(mime_type=mime_type)
            uploaded_file = self.client.files.upload(**upload_kwargs)

            # Wacht expliciet totdat de status ACTIVE is (met een limiet om oneindig wachten te voorkomen)
            _log("   -> Vertaal-Engine verwerkt het bestand... even geduld...")
            max_wait_seconds = 120
            waited = 0
            while uploaded_file.state.name not in ["ACTIVE", "FAILED"] and waited < max_wait_seconds:
                time.sleep(3)
                waited += 3
                uploaded_file = self.client.files.get(name=uploaded_file.name)

            if uploaded_file.state.name != "ACTIVE":
                error_detail = getattr(uploaded_file, "error", None)
                _log(f"   ❌ FOUT: Vertaal-Engine kon dit bestand niet verwerken. Status: {uploaded_file.state.name}"
                     + (f" | Detail: {error_detail}" if error_detail else ""))
                return None

            _log("   -> Bestand is succesvol verwerkt. Bezig met vertalen...")

            system_instruction = GEMINI_SYSTEM_TEMPLATE.format(target_language=target_language)
            formatted_user_prompt = USER_PROMPT.format(target_language=target_language)

            # PDF's expliciet op media_resolution "medium" zetten: Gemini 3 gebruikt
            # standaard een hogere resolutie per pagina die bij lange documenten
            # de input-tokenlimiet kan overschrijden. "medium" is voor OCR/documenten
            # het punt waarna kwaliteit verzadigt (zie Gemini 3 document-processing docs).
            file_part_kwargs = {"file_uri": uploaded_file.uri, "mime_type": uploaded_file.mime_type}
            if mime_type == "application/pdf":
                file_part_kwargs["media_resolution"] = types.MediaResolution.MEDIA_RESOLUTION_MEDIUM

            request_contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(**file_part_kwargs),
                        types.Part.from_text(text=formatted_user_prompt),
                    ],
                )
            ]

            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=request_contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        thinking_config=types.ThinkingConfig(thinking_level="low"),
                        max_output_tokens=65536,
                    ),
                )
            except Exception as gen_err:
                detail = getattr(gen_err, "message", None) or (gen_err.args[0] if getattr(gen_err, "args", None) else str(gen_err))
                _log(f"   ❌ FOUT tijdens vertaal-aanroep (generate_content): {detail}")
                _log(f"      Bestand URI: {uploaded_file.uri} | mime_type: {uploaded_file.mime_type}")
                return None

            translated_text = (response.text or "").strip()
            if not translated_text:
                finish_reason = None
                try:
                    finish_reason = response.candidates[0].finish_reason
                except Exception:
                    pass
                _log(f"   ⚠️ Waarschuwing: Lege reactie ontvangen. (finish_reason: {finish_reason})")
                return None

            return translated_text

        except Exception as e:
            detail = getattr(e, "message", None) or (e.args[0] if getattr(e, "args", None) else str(e))
            _log(f"   ❌ FOUT tijdens uploaden/voorbereiden: {detail}")
            return None
        finally:
            if uploaded_file:
                try:
                    self.client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass