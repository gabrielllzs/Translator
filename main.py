import os
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
import urllib.request
import json
import keyring
import flet as ft

# Importeer onze eigen modules
from engine import GeminiTranslator, SUPPORTED_EXTENSIONS
from interface import TranslatorUI

load_dotenv(Path(__file__).resolve().parent / ".env")

# ── CONFIGURATIE VOOR AFSTANDSBEDIENING / UPDATES ──
CURRENT_VERSION = "1.2.0"
VERSION_URL = "https://raw.githubusercontent.com/gabrielllzs/Translator/refs/heads/main/version.json"

class MainController:
    def __init__(self, page: ft.Page):
        self.page = page
        self.ui = TranslatorUI(page, self.handle_start_translation, self.handle_save_api_key, self.handle_stop_translation)
        self._cancel_event = threading.Event()
        self._load_saved_api_key()

        threading.Thread(target=self._check_for_updates, daemon=True).start()

    def _load_saved_api_key(self):
        """Laad de API-key veilig uit de Windows Kluis (Credential Manager)."""
        try:
            saved_key = keyring.get_password("TranslatorApp", "gemini_api_key")
            if saved_key:
                self.ui.entry_key.value = saved_key
        except Exception as e:
            print(f"Kon sleutel niet veilig laden: {e}")

    def handle_save_api_key(self, api_key):
        if not api_key:
            self.ui.show_error("Waarschuwing", "Het invoerveld is leeg. Vul eerst een sleutel in.")
            return

        try:
            keyring.set_password("TranslatorApp", "gemini_api_key", api_key)

            self.ui.show_info("Succes", "API-key is opgeslagen.")
            self.ui.log("🔒 API-key is opgeslagen")
        except Exception as e:
            self.ui.show_error("Fout", f"Kon de API-key niet opslaan: {e}")

    @staticmethod
    def _parse_version(version_str):
        return tuple(int(part) for part in version_str.split("."))

    def _check_for_updates(self):
        try:
            with urllib.request.urlopen(VERSION_URL, timeout=5) as response:
                data = json.loads(response.read().decode())
                remote_version = data.get("version")
                installer_url = data.get("installer_url")

                if remote_version and self._parse_version(remote_version) > self._parse_version(CURRENT_VERSION):
                    self._prompt_update(remote_version, installer_url)
        except Exception as e:
            print(f"Update check failed: {e}")

    def _prompt_update(self, remote_version, installer_url):
        msg = f"Er is een nieuwe versie ({remote_version}) beschikbaar!\n\nWilt u de update nu automatisch downloaden en installeren?"

        def on_yes():
            self.ui.log(f"📥 Update gevonden ({remote_version}). Downloaden starten...")
            self.ui.set_busy(True)
            threading.Thread(target=self._download_and_run_updater, args=(installer_url,), daemon=True).start()

        self.ui.ask_yes_no("Update Beschikbaar", msg, on_yes)

    def _download_and_run_updater(self, installer_url):
        try:
            temp_dir = Path(os.environ.get("TEMP", "."))
            installer_path = temp_dir / "TranslatorInstaller.exe"

            urllib.request.urlretrieve(installer_url, installer_path)

            self.ui.log("🚀 Installer gedownload. Applicatie wordt herstart...")
            time.sleep(1)

            os.startfile(installer_path)
            self.page.run_task(self.page.window.close)
        except Exception as e:
            self.ui.show_error("Update Fout", f"Kon de update niet installeren: {e}")
            self.ui.set_busy(False)

    def handle_start_translation(self, selected_files: list[Path]):
        target_lang = self.ui.lang_var.get()
        self._cancel_event = threading.Event()
        self.ui.set_busy(True)
        threading.Thread(
            target=self._process_translation_thread,
            args=(selected_files, target_lang),
            daemon=True
        ).start()

    def handle_stop_translation(self):
        self._cancel_event.set()
        self.ui.log("🛑 Stoppen aangevraagd... wordt gestopt na het huidige bestand/deel.")

    def _process_translation_thread(self, selected_files: list[Path], target_lang: str):
        if not selected_files:
            self.ui.log("⚠️ Geen bestanden geselecteerd.")
            self.ui.set_busy(False)
            return

        # AUTOMATISCHE MAP GENERATIE:
        # We pakken de map van het allereerste geselecteerde bestand en maken daar 'bestanden_vertaald' aan
        base_dir = selected_files[0].parent
        output_dir = base_dir / "bestanden_vertaald"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Haal de sleutel op uit het invoerveld van de UI
        api_key = (self.ui.entry_key.value or "").strip()

        if not api_key:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

        if not api_key:
            self.ui.log("❌ FOUT: Geen API-sleutel ingevuld!")
            self.ui.show_error("Key Fout", "Vul a.u.b. eerst je API-sleutel in bovenin het scherm.")
            self.ui.set_busy(False)
            return

        model_name = "gemini-3.1-flash-lite"

        self.ui.log(f"🚀 Starten met {len(selected_files)} bestand(en)...")
        self.ui.update_progress(0, len(selected_files))

        try:
            translator = GeminiTranslator(api_key=api_key, model_name=model_name)
        except Exception as e:
            self.ui.log(f"❌ Initialisatie mislukt: {e}")
            self.ui.set_busy(False)
            return

        success_count = 0
        cancelled = False

        for idx, src_file in enumerate(selected_files, 1):
            if self._cancel_event.is_set():
                cancelled = True
                break

            self.ui.log(f"\n[{idx}/{len(selected_files)}] Verwerken: {src_file.name}")
            dest_file = output_dir / f"{src_file.stem}_{target_lang}.txt"

            success = translator.translate_single_file(
                src_file,
                dest_file,
                target_language=target_lang,
                log_callback=self.ui.log,
                cancel_event=self._cancel_event,
            )

            if success:
                success_count += 1

            self.ui.update_progress(idx, len(selected_files))
            time.sleep(0.5)

        if cancelled or self._cancel_event.is_set():
            self.ui.log(f"\n{'='*40}\n🛑 Gestopt door gebruiker. {success_count} van de {len(selected_files)} bestanden vertaald.")
        else:
            self.ui.log(f"\n{'='*40}\n🎉 Klaar! {success_count} van de {len(selected_files)} bestanden succesvol vertaald.")
        self.ui.log(f"📂 Locatie: {output_dir}")
        self.ui.show_info("Klaar", f"Vertaling voltooid!\n{success_count} bestanden opgeslagen in de map:\n{output_dir.name}")
        self.ui.set_busy(False)


def main_page(page: ft.Page):
    MainController(page)


if __name__ == "__main__":
    ft.run(main_page)
