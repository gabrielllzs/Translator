import time
from pathlib import Path
from google import genai
from google.genai import types

_DEFAULT_MODEL = "gemini-3.1-flash-lite"
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tiff", ".tif"}

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

    def translate_single_file(self, src_path: Path, dest_path: Path, target_language: str = "Dutch", log_callback=None) -> bool:
        """Uploadt, vertaalt en verwijdert een bestand via Gemini."""
        def _log(msg):
            if log_callback:
                log_callback(msg)

        uploaded_file = None
        try:
            _log(f"   -> Uploaden naar Vertaal-Engine")
            uploaded_file = self.client.files.upload(file=src_path)

            while uploaded_file.state.name == "PROCESSING":
                _log("   -> Vertaal-Engine verwerkt het bestand... even geduld...")
                time.sleep(2)
                uploaded_file = self.client.files.get(name=uploaded_file.name)

            if uploaded_file.state.name == "FAILED":
                _log(f"   ❌ FOUT: Vertaal-Engine kon dit bestandstype niet verwerken.")
                return False

            _log(f"   -> Bezig met vertalen...")

            system_instruction = GEMINI_SYSTEM_TEMPLATE.format(target_language=target_language)
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[uploaded_file, USER_PROMPT],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                ),
            )
        
            translated_text = (response.text or "").strip()
            if not translated_text:
                _log("   ⚠️ Waarschuwing: Lege reactie ontvangen.")
                return False

            dest_path.write_text(translated_text, encoding="utf-8")
            _log(f"   ✅ Opgeslagen als: {dest_path.name}")
            return True

        except Exception as e:
            _log(f"   ❌ FOUT tijdens verwerking: {e}")
            return False
        finally:
            if uploaded_file:
                try:
                    self.client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass