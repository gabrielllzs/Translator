import os
import threading
import time
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from pathlib import Path
from dotenv import load_dotenv
import urllib.request
import json

# Importeer onze eigen modules
from engine import GeminiTranslator, SUPPORTED_EXTENSIONS
from interface import TranslatorUI

load_dotenv(Path(__file__).resolve().parent / ".env")

# ── CONFIGURATIE VOOR AFSTANDSBEDIENING / UPDATES ──
CURRENT_VERSION = "1.0.2"  # Verhoog dit telkens als je een nieuwe release uitbrengt!
VERSION_URL = "https://raw.githubusercontent.com/gabrielllzs/Translator/refs/heads/main/version.json"

class MainController:
    def __init__(self):
        self.root = ctk.CTk()
        self.ui = TranslatorUI(self.root, self.handle_start_translation)
        
        self.config_path = Path(__file__).resolve().parent / "config.json"
        self._load_saved_api_key() # Laad de key als die al eens is ingevuld

        # Start een stille achtergrondcontrole voor updates bij het opstarten
        threading.Thread(target=self._check_for_updates, daemon=True).start()

    def _load_saved_api_key(self):
        """Laad de opgeslagen API-key in het UI invoerveld."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    data = json.load(f)
                    saved_key = data.get("api_key", "")
                    if saved_key:
                        self.ui.entry_key.insert(0, saved_key)
            except Exception:
                pass

    def _save_api_key(self, api_key):
        """Slaat de ingevoerde API-key lokaal op."""
        try:
            with open(self.config_path, "w") as f:
                json.dump({"api_key": api_key}, f)
        except Exception as e:
            print(f"Kon config niet opslaan: {e}")

    def run(self):
        self.root.mainloop()

    def _check_for_updates(self):
        """Controleert op GitHub of er een nieuwere versie beschikbaar is."""
        try:
            # Voeg een timeout toe zodat de app niet blijft hangen bij slechte verbinding
            with urllib.request.urlopen(VERSION_URL, timeout=5) as response:
                data = json.loads(response.read().decode())
                remote_version = data.get("version")
                installer_url = data.get("installer_url")
                
                if remote_version and remote_version > CURRENT_VERSION:
                    # Schakel over naar de hoofd-thread om de pop-up te tonen
                    self.root.after(0, lambda: self._prompt_update(remote_version, installer_url))
        except Exception as e:
            # Mislukt stilletjes (bijv. als de gebruiker offline is)
            print(f"Update check failed: {e}")

    def _prompt_update(self, remote_version, installer_url):
        """Vraagt de gebruiker of hij de update wil installeren."""
        msg = f"Er is een nieuwe versie ({remote_version}) beschikbaar!\n\nWilt u de update nu automatisch downloaden en installeren?"
        if messagebox.askyesno("Update Beschikbaar", msg):
            self.ui.log(f"📥 Update gevonden ({remote_version}). Downloaden starten...")
            self.ui.set_busy(True)
            threading.Thread(target=self._download_and_run_updater, args=(installer_url,), daemon=True).start()

    def _download_and_run_updater(self, installer_url):
        """Downloadt de nieuwe installer en sluit de huidige app af."""
        try:
            temp_dir = Path(os.environ.get("TEMP", "."))
            installer_path = temp_dir / "AITranslatorInstaller.exe"
            
            # Download de installer executable van GitHub
            urllib.request.urlretrieve(installer_url, installer_path)
            
            self.ui.log("🚀 Installer gedownload. Applicatie wordt herstart...")
            time.sleep(1)
            
            # Start de gedownloade installer (Windows-specifiek)
            os.startfile(installer_path)
            
            # Sluit de huidige applicatie direct af, zodat main.exe NIET meer vergrendeld is door Windows
            self.root.after(0, self.root.quit)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Update Fout", f"Kon de update niet installeren: {e}"))
            self.root.after(0, lambda: self.ui.set_busy(False))

    def handle_start_translation(self, input_dir: Path, output_dir: Path):
        self.ui.set_busy(True)
        threading.Thread(
            target=self._process_translation_thread, 
            args=(input_dir, output_dir), 
            daemon=True
        ).start()

    def _process_translation_thread(self, input_dir: Path, output_dir: Path):
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

        if not api_key:
            self.ui.log("❌ FOUT: Geen API-sleutel ingevuld!")
            self.ui.show_error("Key Fout", "Vul a.u.b. eerst je API-sleutel in bovenin het scherm.")
            self.ui.set_busy(False)
            return
        
        self._save_api_key(api_key)

        model_name = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash-lite").strip()
        files = [f for f in input_dir.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]

        if not files:
            self.ui.log(f"⚠️ Geen ondersteunde bestanden gevonden in: {input_dir.name}")
            self.ui.show_info("Geen bestanden", "Geen ondersteunde documenten of afbeeldingen gevonden.")
            self.ui.set_busy(False)
            return

        self.ui.log(f"🚀 Starten met {len(files)} bestand(en)...")
        self.ui.update_progress(0, len(files))

        try:
            translator = GeminiTranslator(api_key=api_key, model_name=model_name)
        except Exception as e:
            self.ui.log(f"❌ Initialisatie mislukt: {e}")
            self.ui.set_busy(False)
            return

        success_count = 0
        for idx, src_file in enumerate(files, 1):
            self.ui.log(f"\n[{idx}/{len(files)}] Verwerken: {src_file.name}")
            dest_file = output_dir / f"{src_file.stem}_translated.txt"

            success = translator.translate_single_file(src_file, dest_file, log_callback=self.ui.log)
            
            if success:
                success_count += 1
            
            self.ui.update_progress(idx, len(files))
            time.sleep(0.5)

        self.ui.log(f"\n{'='*40}\n🎉 Klaar! {success_count} van de {len(files)} bestanden succesvol vertaald.")
        self.ui.show_info("Klaar", f"Vertaling voltooid!\n{success_count} bestanden succesvol verwerkt.")
        self.ui.set_busy(False)

if __name__ == "__main__":
    controller = MainController()
    controller.run()