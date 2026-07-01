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
CURRENT_VERSION = "1.0.5"  # Verhoog dit telkens als je een nieuwe release uitbrengt!
# GEFIXT: De URL is nu direct en correct zonder /refs/heads/
VERSION_URL = "https://raw.githubusercontent.com/gabrielllzs/Translator/refs/heads/main/version.json"

class MainController:
    def __init__(self):
        self.root = ctk.CTk()
        self.ui = TranslatorUI(self.root, self.handle_start_translation, self.handle_save_api_key)
        
        self.config_path = Path.home() / ".translator_config.json"
        self._load_saved_api_key()
        
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

    def handle_save_api_key(self, api_key):
        """Verwerkt het handmatig opslaan van de API-key via de knop."""
        if not api_key:
            messagebox.showwarning("Waarschuwing", "Het invoerveld is leeg. Vul eerst een sleutel in.")
            return
            
        try:
            with open(self.config_path, "w") as f:
                json.dump({"api_key": api_key}, f)
            
            messagebox.showinfo("Succes", "API-key succesvol opgeslagen!")
            self.ui.log("💾 API-key succesvol lokaal opgeslagen.")
        except Exception as e:
            messagebox.showerror("Fout", f"Kon de API-key niet opslaan: {e}")
            
    def run(self):
        self.root.mainloop()

    def _check_for_updates(self):
        """Controleert op GitHub of er een nieuwere versie beschikbaar is."""
        try:
            with urllib.request.urlopen(VERSION_URL, timeout=5) as response:
                data = json.loads(response.read().decode())
                remote_version = data.get("version")
                installer_url = data.get("installer_url")
                
                if remote_version and remote_version > CURRENT_VERSION:
                    self.root.after(0, lambda: self._prompt_update(remote_version, installer_url))
        except Exception as e:
            print(f"Update check failed: {e}")

    def _prompt_update(self, remote_version, installer_url):
        msg = f"Er is een nieuwe versie ({remote_version}) beschikbaar!\n\nWilt u de update nu automatisch downloaden en installeren?"
        if messagebox.askyesno("Update Beschikbaar", msg):
            self.ui.log(f"📥 Update gevonden ({remote_version}). Downloaden starten...")
            self.ui.set_busy(True)
            threading.Thread(target=self._download_and_run_updater, args=(installer_url,), daemon=True).start()

    def _download_and_run_updater(self, installer_url):
        try:
            temp_dir = Path(os.environ.get("TEMP", "."))
            installer_path = temp_dir / "TranslatorInstaller.exe"
            
            urllib.request.urlretrieve(installer_url, installer_path)
            
            self.ui.log("🚀 Installer gedownload. Applicatie wordt herstart...")
            time.sleep(1)
            
            os.startfile(installer_path)
            self.root.after(0, self.root.quit)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Update Fout", f"Kon de update niet installeren: {e}"))
            self.root.after(0, lambda: self.ui.set_busy(False))

    def handle_start_translation(self, selected_files: list[Path]):
        """Nieuwe handler die de lijst met geselecteerde bestanden accepteert."""
        self.ui.set_busy(True)
        threading.Thread(
            target=self._process_translation_thread, 
            args=(selected_files,), 
            daemon=True
        ).start()

    def _process_translation_thread(self, selected_files: list[Path]):
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
        api_key = self.ui.entry_key.get().strip()
        
        if not api_key:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

        if not api_key:
            self.ui.log("❌ FOUT: Geen API-sleutel ingevuld!")
            self.ui.show_error("Key Fout", "Vul a.u.b. eerst je API-sleutel in bovenin het scherm.")
            self.ui.set_busy(False)
            return
        
        # Sla de key geruisloos op de achtergrond op
        try:
            with open(self.config_path, "w") as f:
                json.dump({"api_key": api_key}, f)
        except Exception:
            pass

        model_name = (os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite").strip()

        self.ui.log(f"🚀 Starten met {len(selected_files)} bestand(en)...")
        self.ui.update_progress(0, len(selected_files))

        try:
            translator = GeminiTranslator(api_key=api_key, model_name=model_name)
        except Exception as e:
            self.ui.log(f"❌ Initialisatie mislukt: {e}")
            self.ui.set_busy(False)
            return

        success_count = 0
        for idx, src_file in enumerate(selected_files, 1):
            self.ui.log(f"\n[{idx}/{len(selected_files)}] Verwerken: {src_file.name}")
            dest_file = output_dir / f"{src_file.stem}_translated.txt"

            success = translator.translate_single_file(src_file, dest_file, log_callback=self.ui.log)
            
            if success:
                success_count += 1
            
            self.ui.update_progress(idx, len(selected_files))
            time.sleep(0.5)

        self.ui.log(f"\n{'='*40}\n🎉 Klaar! {success_count} van de {len(selected_files)} bestanden succesvol vertaald.")
        self.ui.log(f"📂 Locatie: {output_dir}")
        self.ui.show_info("Klaar", f"Vertaling voltooid!\n{success_count} bestanden opgeslagen in de map:\n{output_dir.name}")
        self.ui.set_busy(False)

if __name__ == "__main__":
    controller = MainController()
    controller.run()