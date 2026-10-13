import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from google import genai
from google.genai import types

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    PdfReader = None
    PdfWriter = None

try:
    from docx_layout import DocxLayout
    from docx_writer import PAGE_MARKER, build_docx, markdown_to_text
except ImportError:
    DocxLayout = None
    build_docx = None
    markdown_to_text = None
    PAGE_MARKER = "<<<PAGE>>>"

try:
    import fitz
    import pdf_layout
except ImportError:
    fitz = None
    pdf_layout = None

# Zichtbare modelnaam in de UI -> technische modelnaam achter de schermen.
# De UI toont bewust alleen "Vertaler-nieuw" / "Vertaler-oud", nooit de onderliggende naam.
TRANSLATION_MODELS = {
    "Vertaler-nieuw": "gemini-3.6-flash",
    "Vertaler-oud": "gemini-3.5-flash-lite",
}
DEFAULT_MODEL_LABEL = "Vertaler-oud"
# Hoeveel het model "nadenkt" per model. Bij "low" liet gemini-3.6-flash in
# tests geregeld tabelcellen onvertaald; "medium" loste dat op.
THINKING_LEVELS = {"gemini-3.6-flash": "medium"}
_DEFAULT_MODEL = TRANSLATION_MODELS[DEFAULT_MODEL_LABEL]
# Tekstbestanden worden niet geüpload maar direct als tekst meegestuurd.
TEXT_EXTENSIONS = {".txt"}
# Word-bestanden: alleen de tekst wordt vervangen, de layout blijft staan.
WORD_EXTENSIONS = {".docx"}
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tiff", ".tif"} | TEXT_EXTENSIONS | WORD_EXTENSIONS

# Hoe de vertaling wordt opgeleverd (zichtbare naam in de UI -> interne code).
OUTPUT_FORMATS = {
    "Tekst (.txt)": "txt",
    "Word (alleen tekst)": "docx",
    "Layout behouden": "layout",
}
DEFAULT_OUTPUT_FORMAT = "Tekst (.txt)"

# Talen die van rechts naar links geschreven worden (voor Word-uitvoer).
RTL_LANGUAGES = {"arabic", "arabisch", "العربية"}

# Maximaal aantal tekens per verzoek bij tekstbestanden. Ruim onder de
# output-limiet, want een vertaling is vaak langer dan het origineel.
TEXT_CHARS_PER_CHUNK = 30000

# Maximaal aantal tekens aan Word-alinea's per verzoek.
DOCX_CHARS_PER_BATCH = 12000
# Ook het aantal tekstblokken per verzoek begrenzen: bij lange lijsten raakt het
# model de tel kwijt (items samengevoegd of overgeslagen).
DOCX_ITEMS_PER_BATCH = 40
# Zoveel delen tegelijk vertalen (scheelt veel tijd bij grote documenten).
PARALLEL_BATCHES = 4

# Gemini 3.x Flash(-Lite) heeft een output-limiet van ~64K tokens. Een dicht,
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
- Reproduce the document's structure with simple Markdown, top-to-bottom, following the document's layout flow:
  - `#` for the main document title, `##` for section headings. Only use headings for real titles/headings.
  - `**bold**` for text that is bold or clearly emphasised in the original, `*italic*` for italic text.
  - Tables, and forms laid out as label/value grids, as Markdown pipe tables (`| Name | John Smith |`), with the same rows and columns as the original. Use `<br>` for a line break inside a cell.
    The text inside every cell must be translated into {target_language} too; never copy cell text in the original language.
  - `- ` for bulleted lists. Keep numbering exactly as written in the original (e.g. `1.`, `a)`, `II.`).
  - Every other line or paragraph of the original on its own line.
- Write stamps, seals, signatures, logos, photos and handwritten notes in italics between square brackets, with a short label in {target_language}, e.g. *[Stamp: Municipality of Utrecht]*, *[Signature]*, *[Handwritten: 12 May]*. Translate any readable text in them.
- Where a new page of the original starts, put a line containing only {page_marker}. Do not put it before the first page.
- Do not wrap the output in code fences.
- Translate the full source content; do not omit sentences. This includes every table cell, header, footer, and footnote.
- Never include UI elements in the trsnalation output (e.g., "Click here", "Select a file", "Submit", "Cancel")."""

USER_PROMPT = "Read every word on the uploaded document (including stamps, handwritten text, and notes) and translate it fully into {target_language} according to the system rules. Everything you output, including text inside tables, must be in {target_language}."

TEXT_USER_PROMPT = "Translate the following text fully into {target_language} according to the system rules. Output only the translation.\n\n<text>\n{text}\n</text>"

DOCX_SYSTEM_TEMPLATE = """You are a professional translator (legal, official, and documentary use).

You receive a JSON array of text segments taken from one document, in reading order.
Translate every segment into {target_language}.

Rules:
- Return a JSON array of strings with exactly the same number of items, in the same order. Item N is the translation of input item N.
- Never merge, split, drop, or add items.
- Some segments contain markers like <r1>...</r1>, <r2>...</r2>. They mark parts with their own formatting (for example a bold word).
  Keep every marker exactly once, with the translation of that part inside it. You may change the order of the markers if the grammar of {target_language} requires it. Never add new markers.
- Stay faithful: do not invent facts, names, dates, or places. Preserve numbers, dates, codes, and reference numbers exactly.
- If a segment is already in {target_language}, return it unchanged.
- The text may come from OCR and contain recognition errors (e.g. "warden" for "worden", "coca'ine" for "cocaïne"). Translate what is clearly meant, but copy names, numbers, and references exactly as written.
- No explanations, notes, or preambles."""

DOCX_USER_PROMPT = "Translate these segments into {target_language}:\n{segments}"


class GeminiTranslator:
    def __init__(self, api_key: str, model_name: str = None):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name or _DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Publieke methode: vertaalt één bronbestand (evt. in meerdere delen)
    # ------------------------------------------------------------------
    def translate_single_file(self, src_path: Path, dest_path: Path, target_language: str = "Nederlands",
                              log_callback=None, output_format: str = "txt") -> Path | None:
        """Vertaalt één bestand. Geeft het pad van de vertaling terug, of None bij een fout.

        output_format (zie OUTPUT_FORMATS):
          - "txt":    alleen de vertaalde tekst, als .txt (de klant doet zelf de opmaak);
          - "docx":   de vertaalde tekst in een eenvoudig Word-document;
          - "layout": zo dicht mogelijk bij het origineel: digitale PDF's worden een PDF met
                      de originele layout, Word-bestanden houden hun opmaak, scans worden
                      een net opgebouwd Word-document.
        De extensie van dest_path wordt aangepast aan wat er werkelijk gemaakt wordt.
        """
        def _log(msg):
            if log_callback:
                log_callback(msg)

        self._output_format = output_format
        self._output_path = None

        if src_path.suffix.lower() != ".pdf":
            ok = self._translate_by_type(src_path, dest_path, target_language, _log)
            return self._output_path if ok else None

        page_count = self._get_pdf_page_count(src_path, _log)
        if output_format == "layout":
            kinds = self._classify_pdf(src_path, _log)
            if kinds and "digital" in kinds:
                pdf_dest = dest_path.with_suffix(".pdf")
                ok = self._translate_pdf_in_place(src_path, pdf_dest, target_language, kinds, _log)
                return pdf_dest if ok else None
            _log("   -> Gescande PDF: de vertaling wordt een net opgebouwd Word-document.")

        ok = self._translate_by_type(src_path, dest_path, target_language, _log, page_count)
        return self._output_path if ok else None

    def _translate_by_type(self, src_path: Path, dest_path: Path, target_language: str, _log,
                           page_count: int | None = None) -> bool:
        if src_path.suffix.lower() in TEXT_EXTENSIONS:
            return self._translate_text_file(src_path, dest_path, target_language, _log)

        if src_path.suffix.lower() in WORD_EXTENSIONS:
            if self._output_format == "layout":
                return self._translate_docx(src_path, dest_path.with_suffix(".docx"), target_language, _log)
            return self._translate_docx_as_text(src_path, dest_path, target_language, _log)

        if src_path.suffix.lower() == ".doc":
            _log("   ❌ Het oude Word-formaat (.doc) wordt niet ondersteund. "
                 "Open het bestand in Word en kies 'Opslaan als' → .docx.")
            return False

        if src_path.suffix.lower() == ".pdf":
            if page_count and page_count > PAGES_PER_CHUNK:
                return self._translate_large_pdf(src_path, dest_path, target_language, page_count, _log)

        # Klein bestand (of geen PDF): gewoon in één keer verwerken.
        translated_text = self._translate_chunk(src_path, target_language, _log)
        if translated_text is None:
            return False

        return self._save_translation(translated_text, src_path, dest_path, target_language, _log)

    def _save_translation(self, translated_text: str, src_path: Path, dest_path: Path, target_language: str,
                          _log) -> bool:
        """Sla de vertaling (met Markdown-structuur van het model) op als .txt of Word."""
        if getattr(self, "_output_format", "txt") == "txt":
            dest_path = dest_path.with_suffix(".txt")
            try:
                dest_path.write_text(markdown_to_text(translated_text), encoding="utf-8")
            except Exception as e:
                _log(f"   ❌ FOUT: Kon het tekstbestand niet opslaan: {e}")
                return False
        else:
            dest_path = dest_path.with_suffix(".docx")
            if build_docx is None:
                _log("   ❌ 'python-docx' is niet geïnstalleerd — kan geen Word-bestand maken.")
                _log("      Installeer met: pip install python-docx")
                return False
            try:
                build_docx(translated_text, dest_path, src_path.name, target_language, self._is_rtl(target_language))
            except Exception as e:
                _log(f"   ❌ FOUT: Kon het Word-bestand niet opslaan: {e}")
                return False
        self._output_path = dest_path
        _log(f"   ✅ Opgeslagen als: {dest_path.name}")
        return True

    @staticmethod
    def _is_rtl(target_language: str) -> bool:
        return target_language.strip().lower() in RTL_LANGUAGES

    # ------------------------------------------------------------------
    # Tekstbestanden: inhoud lezen en direct als tekst laten vertalen
    # ------------------------------------------------------------------
    def _read_text_file(self, src_path: Path) -> str:
        raw = src_path.read_bytes()
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        for encoding in ("utf-8-sig", "cp1256", "cp1252"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _split_text(self, text: str) -> list[str]:
        """Knip lange tekst op alinea-grenzen in stukken van max. TEXT_CHARS_PER_CHUNK tekens."""
        chunks, current = [], ""
        for paragraph in text.split("\n\n"):
            while len(paragraph) > TEXT_CHARS_PER_CHUNK:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(paragraph[:TEXT_CHARS_PER_CHUNK])
                paragraph = paragraph[TEXT_CHARS_PER_CHUNK:]
            if current and len(current) + len(paragraph) + 2 > TEXT_CHARS_PER_CHUNK:
                chunks.append(current)
                current = paragraph
            else:
                current = f"{current}\n\n{paragraph}" if current else paragraph
        if current:
            chunks.append(current)
        return chunks

    def _translate_text_file(self, src_path: Path, dest_path: Path, target_language: str, _log) -> bool:
        try:
            text = self._read_text_file(src_path).strip()
        except Exception as e:
            _log(f"   ❌ FOUT: Kon het tekstbestand niet lezen: {e}")
            return False

        if not text:
            _log("   ⚠️ Het tekstbestand is leeg, er is niets om te vertalen.")
            return False
        return self._translate_plain_text(text, src_path, dest_path, target_language, _log)

    def _translate_docx_as_text(self, src_path: Path, dest_path: Path, target_language: str, _log) -> bool:
        """Word-bestand vertalen tot losse tekst (zonder de opmaak van het origineel)."""
        if DocxLayout is None:
            _log("   ❌ 'python-docx' is niet geïnstalleerd — kan geen Word-bestanden vertalen.")
            return False
        try:
            text = DocxLayout(src_path).plain_text().strip()
        except Exception as e:
            _log(f"   ❌ FOUT: Kon het Word-bestand niet openen (beveiligd of beschadigd?): {e}")
            return False
        if not text:
            _log("   ⚠️ Geen vertaalbare tekst gevonden in dit Word-bestand.")
            return False
        return self._translate_plain_text(text, src_path, dest_path, target_language, _log)

    def _translate_plain_text(self, text: str, src_path: Path, dest_path: Path, target_language: str, _log) -> bool:
        chunks = self._split_text(text)
        translated_parts = []
        for idx, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                _log(f"   [Deel {idx}/{len(chunks)}] Tekst vertalen...")
            else:
                _log("   -> Tekst wordt vertaald...")
            prompt = TEXT_USER_PROMPT.format(target_language=target_language, text=chunk)
            translated = self._generate([types.Part.from_text(text=prompt)], target_language, _log)
            if translated is None:
                return False
            translated_parts.append(translated)

        return self._save_translation("\n\n".join(translated_parts), src_path, dest_path, target_language, _log)

    # ------------------------------------------------------------------
    # Word-documenten: tekst vervangen, layout behouden
    # ------------------------------------------------------------------
    def _translate_docx(self, src_path: Path, dest_path: Path, target_language: str, _log) -> bool:
        if DocxLayout is None:
            _log("   ❌ 'python-docx' is niet geïnstalleerd — kan geen Word-bestanden vertalen.")
            _log("      Installeer met: pip install python-docx")
            return False

        try:
            document = DocxLayout(src_path)
            segments = document.segments()
        except Exception as e:
            _log(f"   ❌ FOUT: Kon het Word-bestand niet openen (beveiligd of beschadigd?): {e}")
            return False

        if not segments:
            _log("   ⚠️ Geen vertaalbare tekst gevonden in dit Word-bestand.")
            return False

        translations = self._translate_segments([seg.source for seg in segments], target_language, _log)
        if translations is None:
            _log("   ❌ Vertaling van het Word-bestand is afgebroken.")
            return False

        rtl = self._is_rtl(target_language)
        fallbacks = 0
        for seg in segments:
            if not seg.apply(translations[seg.source]):
                fallbacks += 1
            seg.set_direction(rtl)

        if fallbacks:
            _log(f"   ⚠️ Bij {fallbacks} alinea('s) kon de opmaak binnen de zin (bijv. één vet woord) "
                 "niet precies worden overgenomen; de tekst is wel vertaald.")

        try:
            document.save(dest_path)
        except Exception as e:
            _log(f"   ❌ FOUT: Kon het vertaalde Word-bestand niet opslaan: {e}")
            return False

        self._output_path = dest_path
        _log(f"   ✅ Opgeslagen als: {dest_path.name}")
        return True

    def _translate_docx_batch(self, sources: list[str], target_language: str, _log) -> list[str] | None:
        """Vertaal een lijst tekstblokken; bij een onbruikbaar antwoord in kleinere stukken opnieuw."""
        prompt = DOCX_USER_PROMPT.format(
            target_language=target_language,
            segments=json.dumps(sources, ensure_ascii=False),
        )
        response = self._generate(
            [types.Part.from_text(text=prompt)], target_language, _log,
            system_instruction=DOCX_SYSTEM_TEMPLATE.format(target_language=target_language),
            json_list=True,
        )
        if response is None:
            return None

        try:
            result = json.loads(response)
        except ValueError:
            result = None
        if isinstance(result, list) and len(result) == len(sources) and all(isinstance(r, str) for r in result):
            return result

        if len(sources) == 1:
            # Liever dit ene stukje onvertaald laten dan het hele document afbreken.
            _log(f"   ⚠️ Eén tekstblok kon niet vertaald worden en blijft in de originele taal: "
                 f"{sources[0][:60]!r}")
            return list(sources)

        _log(f"   -> Antwoord klopte niet helemaal; deze {len(sources)} tekstblokken worden "
             "in twee kleinere delen opnieuw vertaald...")
        half = len(sources) // 2
        first = self._translate_docx_batch(sources[:half], target_language, _log)
        if first is None:
            return None
        second = self._translate_docx_batch(sources[half:], target_language, _log)
        if second is None:
            return None
        return first + second

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

    # ------------------------------------------------------------------
    # Digitale PDF's: tekst op de pagina zelf vervangen (layout blijft gelijk)
    # ------------------------------------------------------------------
    def _classify_pdf(self, src_path: Path, _log) -> list[str] | None:
        if pdf_layout is None:
            return None
        try:
            with fitz.open(src_path) as doc:
                return pdf_layout.classify_pages(doc)
        except Exception as e:
            _log(f"   ⚠️ Kon de PDF niet analyseren ({e}); wordt als scan behandeld.")
            return None

    def _translate_pdf_in_place(self, src_path: Path, dest_path: Path, target_language: str,
                                kinds: list[str], _log) -> bool:
        scans = [n for n, kind in enumerate(kinds) if kind == "scan"]
        _log(f"   -> Digitale PDF herkend: de vertaling wordt een PDF met de originele layout.")
        if scans:
            _log(f"   -> {len(scans)} gescande pagina('s) gevonden; de vertaling daarvan komt "
                 "als extra pagina direct na het origineel.")
        rtl = self._is_rtl(target_language)

        try:
            doc = fitz.open(src_path)
        except Exception as e:
            _log(f"   ❌ FOUT: Kon de PDF niet openen (beveiligd of beschadigd?): {e}")
            return False

        with doc:
            segments_by_page = {
                n: pdf_layout.extract_segments(doc[n], n) for n, kind in enumerate(kinds) if kind == "digital"
            }
            pdf_layout.mark_letterhead(segments_by_page, {n: doc[n].rect.height for n in segments_by_page})
            all_segments = [seg for segs in segments_by_page.values() for seg in segs if seg.translatable]
            translations = self._translate_segments([seg.source for seg in all_segments], target_language, _log)
            if translations is None:
                _log("   ❌ Vertaling van de PDF is afgebroken.")
                return False
            for seg in all_segments:
                seg.translation = translations[seg.source]

            _log("   -> Vertaalde tekst op de pagina's plaatsen...")
            last_logged = [0]

            def progress(done, total):
                # Bij grote documenten af en toe laten zien dat er voortgang is.
                if total >= 20 and done - last_logged[0] >= max(10, total // 10):
                    last_logged[0] = done
                    _log(f"      pagina {done}/{total}")

            try:
                out, fallbacks = pdf_layout.translate_document(doc, segments_by_page, rtl, progress)
            except Exception as e:
                _log(f"   ❌ FOUT bij het plaatsen van de vertaalde tekst: {e}")
                return False
            if fallbacks:
                _log(f"   ⚠️ Bij {fallbacks} tekstblok(ken) kon de opmaak binnen de zin (bijv. één vet woord) "
                     "niet precies worden overgenomen; de tekst is wel vertaald.")

            # Gescande pagina's: van achter naar voren, zodat de paginanummers kloppen.
            with tempfile.TemporaryDirectory(prefix="translator_scan_") as tmp_dir:
                for page_number in reversed(scans):
                    _log(f"   -> Gescande pagina {page_number + 1} vertalen...")
                    single = fitz.open()
                    single.insert_pdf(out, from_page=page_number, to_page=page_number)
                    single_path = Path(tmp_dir) / f"pagina_{page_number + 1}.pdf"
                    single.save(single_path)
                    single.close()
                    markdown = self._translate_chunk(single_path, target_language, _log)
                    if markdown is None:
                        return False
                    pdf_layout.append_translation_page(out, page_number, pdf_layout.markdown_to_html(markdown, rtl), rtl)

            try:
                out.save(dest_path, garbage=4, deflate=True)
                out.close()
            except Exception as e:
                _log(f"   ❌ FOUT: Kon de vertaalde PDF niet opslaan: {e}")
                return False

        _log(f"   ✅ Opgeslagen als: {dest_path.name}")
        return True

    def _translate_segments(self, sources: list[str], target_language: str, _log) -> dict | None:
        """Vertaal losse tekststukken in groepen; dezelfde tekst maar één keer."""
        unique_sources = list(dict.fromkeys(sources))
        batches, current, size = [], [], 0
        for source in unique_sources:
            if current and (size + len(source) > DOCX_CHARS_PER_BATCH or len(current) >= DOCX_ITEMS_PER_BATCH):
                batches.append(current)
                current, size = [], 0
            current.append(source)
            size += len(source)
        if current:
            batches.append(current)

        _log(f"   -> {len(sources)} tekstblokken gevonden, vertalen in {len(batches)} deel/delen...")

        def run(numbered):
            idx, batch = numbered
            result = self._translate_docx_batch(batch, target_language, _log)
            if result is not None and len(batches) > 1:
                _log(f"   [Deel {idx}/{len(batches)}] klaar ({len(batch)} tekstblokken)")
            return batch, result

        translations = {}
        with ThreadPoolExecutor(max_workers=PARALLEL_BATCHES) as pool:
            for batch, result in pool.map(run, enumerate(batches, 1)):
                if result is None:
                    return None
                translations.update(zip(batch, result))
        return translations

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

        # Elk deel begint op een nieuwe pagina van het origineel.
        full_text = f"\n{PAGE_MARKER}\n".join(translated_parts)
        _log(f"\n   -> Alle {total_chunks} delen samengevoegd.")
        return self._save_translation(full_text, src_path, dest_path, target_language, _log)

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

            formatted_user_prompt = USER_PROMPT.format(target_language=target_language)

            # PDF's expliciet op media_resolution "medium" zetten: Gemini 3 gebruikt
            # standaard een hogere resolutie per pagina die bij lange documenten
            # de input-tokenlimiet kan overschrijden. "medium" is voor OCR/documenten
            # het punt waarna kwaliteit verzadigt (zie Gemini 3 document-processing docs).
            file_part_kwargs = {"file_uri": uploaded_file.uri, "mime_type": uploaded_file.mime_type}
            if mime_type == "application/pdf":
                file_part_kwargs["media_resolution"] = types.MediaResolution.MEDIA_RESOLUTION_MEDIUM

            parts = [
                types.Part.from_uri(**file_part_kwargs),
                types.Part.from_text(text=formatted_user_prompt),
            ]
            translated_text = self._generate(parts, target_language, _log)
            if translated_text is None:
                _log(f"      Bestand URI: {uploaded_file.uri} | mime_type: {uploaded_file.mime_type}")
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

    # ------------------------------------------------------------------
    # De daadwerkelijke vertaal-aanroep (gedeeld door bestanden en tekst)
    # ------------------------------------------------------------------
    def _generate(self, parts: list, target_language: str, _log,
                  system_instruction: str = None, json_list: bool = False) -> str | None:
        if system_instruction is None:
            system_instruction = GEMINI_SYSTEM_TEMPLATE.format(target_language=target_language, page_marker=PAGE_MARKER)
        config_kwargs = {}
        if json_list:
            # Dwingt een JSON-lijst van strings af (gebruikt voor Word-alinea's).
            config_kwargs = {"response_mime_type": "application/json", "response_schema": list[str]}
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVELS.get(self.model_name, "low")),
                    max_output_tokens=65536,
                    **config_kwargs,
                ),
            )
        except Exception as gen_err:
            detail = getattr(gen_err, "message", None) or (gen_err.args[0] if getattr(gen_err, "args", None) else str(gen_err))
            _log(f"   ❌ FOUT tijdens vertaal-aanroep (generate_content): {detail}")
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
