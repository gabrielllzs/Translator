import os
import shutil
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from pathlib import Path
from dotenv import load_dotenv
import urllib.request
import json
import keyring


# Importeer onze eigen modules
from engine import GeminiTranslator, SUPPORTED_EXTENSIONS, TRANSLATION_MODELS, DEFAULT_MODEL_LABEL, OUTPUT_FORMATS, DEFAULT_OUTPUT_FORMAT
from interface import TranslatorUI

load_dotenv(Path(__file__).resolve().parent / ".env")

# ── CONFIGURATIE VOOR AFSTANDSBEDIENING / UPDATES ──
CURRENT_VERSION = "1.3.1"
VERSION_URL = "https://raw.githubusercontent.com/gabrielllzs/Translator/refs/heads/main/version.json"

class MainController:
    def __init__(self):
        # Eigen app-ID, zodat Windows in de taakbalk ons icoon toont i.p.v. dat van Python.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SyncIT.FileTranslator")
        except Exception:
            pass
        self.root = ctk.CTk()
        self.ui = TranslatorUI(self.root, self.handle_start_translation, self.handle_save_api_key, self.handle_download)

        # Vertalingen komen eerst in een tijdelijke map terecht; de gebruiker
        # kiest daarna zelf via "Downloaden" waar ze naartoe gaan.
        self.temp_dir = Path(tempfile.mkdtemp(prefix="translator_"))
        self.translated_files: list[Path] = []
        self.results_saved = True
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        self.config_path = Path.home() / ".translator_config.json"
        self._load_saved_api_key()
        self._load_saved_model()
        self.ui.model_var.trace_add("write", lambda *_: self._update_config(model=self.ui.model_var.get()))
        self.ui.output_var.trace_add("write", lambda *_: self._update_config(output_format=self.ui.output_var.get()))
        
        threading.Thread(target=self._check_for_updates, daemon=True).start()

    def _load_saved_api_key(self):
        """Laad de API-key veilig uit de Windows Kluis (Credential Manager)."""
        try:
            saved_key = keyring.get_password("TranslatorApp", "gemini_api_key")
            if saved_key:
                self.ui.entry_key.insert(0, saved_key)
        except Exception as e:
            print(f"Kon sleutel niet veilig laden: {e}")

    def _read_config(self) -> dict:
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _update_config(self, **values):
        """Werk losse velden in het configbestand bij zonder de rest te overschrijven."""
        config = self._read_config()
        config.update(values)
        try:
            with open(self.config_path, "w") as f:
                json.dump(config, f)
        except Exception:
            pass

    def _load_saved_model(self):
        """Zet het vertaalmodel terug op wat de gebruiker de vorige keer koos."""
        saved_model = self._read_config().get("model")
        if saved_model in TRANSLATION_MODELS:
            self.ui.model_var.set(saved_model)
        saved_output = self._read_config().get("output_format")
        if saved_output in OUTPUT_FORMATS:
            self.ui.output_var.set(saved_output)

    def handle_save_api_key(self, api_key):
        if not api_key:
            messagebox.showwarning("Waarschuwing", "Het invoerveld is leeg. Vul eerst een sleutel in.")
            return
            
        try:
            keyring.set_password("TranslatorApp", "gemini_api_key", api_key)
            
            messagebox.showinfo("Succes", "API-key is opgeslagen.")
            self.ui.log("🔒 API-key is opgeslagen")
        except Exception as e:
            messagebox.showerror("Fout", f"Kon de API-key niet opslaan: {e}")
            
    def run(self):
        self.root.mainloop()

    def _on_close(self):
        if not self.results_saved:
            if not messagebox.askyesno(
                "Niet opgeslagen",
                "Er zijn vertalingen die je nog niet hebt gedownload.\n\nToch afsluiten? De vertalingen gaan dan verloren.",
            ):
                return
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.root.destroy()

    def handle_download(self):
        """Laat de gebruiker kiezen waar de vertaalde bestanden naartoe gaan."""
        files = [f for f in self.translated_files if f.exists()]
        if not files:
            messagebox.showwarning("Geen vertalingen", "Er zijn nog geen vertaalde bestanden om te downloaden.")
            return

        if len(files) == 1:
            src = files[0]
            target = filedialog.asksaveasfilename(
                title="Vertaling opslaan als",
                initialfile=src.name,
                defaultextension=src.suffix,
                filetypes=[(f"{src.suffix.upper().lstrip('.')}-bestand", f"*{src.suffix}"), ("Alle bestanden", "*.*")],
            )
            if not target:
                return
            targets = [(src, Path(target))]
            location = Path(target)
        else:
            folder = filedialog.askdirectory(title="Kies een map voor de vertalingen")
            if not folder:
                return
            folder = Path(folder)
            existing = [f.name for f in files if (folder / f.name).exists()]
            if existing and not messagebox.askyesno(
                "Bestanden overschrijven?",
                f"{len(existing)} bestand(en) bestaan al in deze map:\n"
                + "\n".join(existing[:5]) + ("\n..." if len(existing) > 5 else "")
                + "\n\nOverschrijven?",
            ):
                return
            targets = [(f, folder / f.name) for f in files]
            location = folder

        try:
            for src, dest in targets:
                shutil.copy2(src, dest)
        except Exception as e:
            messagebox.showerror("Fout", f"Kon de vertalingen niet opslaan: {e}")
            return

        self.results_saved = True
        self.ui.log(f"💾 {len(targets)} bestand(en) opgeslagen in: {location}")

    def _check_for_updates(self):
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
        target_lang = self.ui.lang_var.get()
        self.ui.set_busy(True)
        threading.Thread(
            target=self._process_translation_thread, 
            args=(selected_files, target_lang), 
            daemon=True
        ).start()

    def _process_translation_thread(self, selected_files: list[Path], target_lang: str):        
        if not selected_files:
            self.ui.log("⚠️ Geen bestanden geselecteerd.")
            self.ui.set_busy(False) 
            return

        # Elke nieuwe vertaalronde begint met een lege tijdelijke map.
        output_dir = self.temp_dir / "vertaald"
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        self.translated_files = []
        self.results_saved = True

        # Haal de sleutel op uit het invoerveld van de UI
        api_key = self.ui.entry_key.get().strip()
        
        if not api_key:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

        if not api_key:
            self.ui.log("❌ FOUT: Geen API-sleutel ingevuld!")
            self.ui.show_error("Key Fout", "Vul a.u.b. eerst je API-sleutel in bovenin het scherm.")
            self.ui.set_busy(False)
            return
        
        self._update_config(api_key=api_key)

        model_label = self.ui.model_var.get()
        model_name = TRANSLATION_MODELS.get(model_label, TRANSLATION_MODELS[DEFAULT_MODEL_LABEL])

        output_label = self.ui.output_var.get()
        output_format = OUTPUT_FORMATS.get(output_label, OUTPUT_FORMATS[DEFAULT_OUTPUT_FORMAT])

        self.ui.log(f"🚀 Starten met {len(selected_files)} bestand(en) via {model_label} — uitvoer: {output_label}...")
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
            # De engine past de extensie aan (.txt, .docx of .pdf).
            dest_file = output_dir / f"{src_file.stem}_{target_lang}.txt"

            result_file = translator.translate_single_file(
                src_file, 
                dest_file, 
                target_language=target_lang,
                log_callback=self.ui.log,
                output_format=output_format,
            )
            
            if result_file:
                success_count += 1
                self.translated_files.append(result_file)
            
            self.ui.update_progress(idx, len(selected_files))
            time.sleep(0.5)

        self.ui.log(f"\n{'='*40}\n🎉 Klaar! {success_count} van de {len(selected_files)} bestanden succesvol vertaald.")
        self.ui.set_busy(False)
        if success_count:
            self.results_saved = False
            self.ui.log("⬇ Klik op 'Downloaden' om te kiezen waar je de vertaling(en) wilt opslaan.")
            self.root.after(0, lambda: self.ui.set_download_available(True))
            self.ui.show_info("Klaar", f"Vertaling voltooid!\n{success_count} van de {len(selected_files)} bestanden vertaald.\n\nKlik op 'Downloaden' om ze op te slaan.")

if __name__ == "__main__":
    controller = MainController()
    controller.run()