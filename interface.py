import customtkinter as ctk
from tkinter import filedialog, messagebox
from pathlib import Path

# Thema instellingen (Opties: "System", "Dark", "Light")
ctk.set_appearance_mode("System")  
# Kleurenschema (Opties: "blue", "green", "dark-blue")
ctk.set_default_color_theme("blue") 

class TranslatorUI:
    def __init__(self, root, start_callback, save_key_callback):
        self.root = root
        self.start_callback = start_callback
        self.save_key_callback = save_key_callback
        
        # Hier slaan we de geselecteerde Path-objecten op
        self.selected_files = []
        
        self.root.title("File Translator")
        self.root.geometry("700x500")
        self.root.minsize(600, 400)

        self.setup_ui()
        
    def setup_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1) 

        # ── API KEY FRAME ──
        frame_key = ctk.CTkFrame(self.root, corner_radius=10)
        frame_key.grid(row=0, column=0, padx=15, pady=10, sticky="ew")
        frame_key.columnconfigure(1, weight=1)

        lbl_key_title = ctk.CTkLabel(frame_key, text="API Instellingen", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_key_title.grid(row=0, column=0, columnspan=3, sticky="w", padx=15, pady=5)

        ctk.CTkLabel(frame_key, text="API Key:").grid(row=1, column=0, sticky="w", padx=15, pady=5)
        self.entry_key = ctk.CTkEntry(frame_key, placeholder_text="Plak hier je API sleutel...", show="*")
        self.entry_key.grid(row=1, column=1, sticky="ew", padx=5, pady=5)

        ctk.CTkButton(frame_key, text="Opslaan", width=90, command=self.on_save_key_click).grid(row=1, column=2, padx=15, pady=5)

        # ── HET FRAME DEFINIËREN (Eerst doen!) ──
        frame_files = ctk.CTkFrame(self.root, corner_radius=10)
        frame_files.grid(row=1, column=0, padx=15, pady=10, sticky="ew")
        frame_files.columnconfigure(1, weight=1)

        # ── TAAL SELECTIE (Nu pas in frame_files plaatsen) ──
        lbl_lang_title = ctk.CTkLabel(frame_files, text="Taal Instellingen", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_lang_title.grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=5)

        self.lang_var = ctk.StringVar(value="Dutch")
        ctk.CTkLabel(frame_files, text="Doeltaal:").grid(row=1, column=0, padx=15, pady=5, sticky="w")
        self.lang_menu = ctk.CTkOptionMenu(frame_files, variable=self.lang_var, values=["Dutch", "Arabic"])
        self.lang_menu.grid(row=1, column=1, padx=15, pady=5, sticky="ew")

        # ── BESTANDEN SELECTIE (Ook in hetzelfde frame) ──
        lbl_file_title = ctk.CTkLabel(frame_files, text="Bestanden Selecteren", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_file_title.grid(row=2, column=0, columnspan=3, sticky="w", padx=15, pady=5)

        ctk.CTkLabel(frame_files, text="Documenten:").grid(row=3, column=0, sticky="w", padx=15, pady=5)
        self.entry_files = ctk.CTkEntry(frame_files, placeholder_text="Kies één of meerdere bestanden...")
        self.entry_files.grid(row=3, column=1, sticky="ew", padx=5, pady=5)
        self.entry_files.configure(state="readonly")
        ctk.CTkButton(frame_files, text="Selecteer...", width=100, command=self.browse_files).grid(row=3, column=2, padx=15, pady=5)

        # ── ACTIE FRAME ──
        frame_actions = ctk.CTkFrame(self.root, fg_color="transparent")
        frame_actions.grid(row=2, column=0, padx=15, pady=5, sticky="ew")
        frame_actions.columnconfigure(1, weight=1)
        
        self.btn_start = ctk.CTkButton(frame_actions, text="Start Vertaling", font=ctk.CTkFont(weight="bold"), command=self.on_start_click)
        self.btn_start.grid(row=0, column=0, padx=5, pady=5)
        
        self.progress_bar = ctk.CTkProgressBar(frame_actions)
        self.progress_bar.grid(row=0, column=1, sticky="ew", padx=15, pady=5)
        self.progress_bar.set(0)

        # ── LOG FRAME ──
        frame_log = ctk.CTkFrame(self.root, corner_radius=10)
        frame_log.grid(row=3, column=0, padx=15, pady=10, sticky="nsew")
        frame_log.columnconfigure(0, weight=1)
        frame_log.rowconfigure(1, weight=1)

        lbl_log_title = ctk.CTkLabel(frame_log, text="Voortgang & Log", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_log_title.grid(row=0, column=0, sticky="w", padx=15, pady=5)

        self.txt_log = ctk.CTkTextbox(frame_log, font=("Consolas", 12))
        self.txt_log.grid(row=1, column=0, sticky="nsew", padx=15, pady=10)
        self.txt_log.configure(state="disabled")

        self.log("Applicatie opgestart. Selecteer je bestanden, vul je API-key in en klik op 'Start Vertaling'.")

    def on_save_key_click(self):
        api_key = self.entry_key.get().strip()
        self.save_key_callback(api_key)
    
    def browse_files(self):
        """Opent de verkenner om meerdere specifieke bestanden te kiezen."""
        file_types = [
            ("Ondersteunde bestanden", "*.pdf *.jpg *.jpeg *.png *.webp *.heic *.tiff *.tif"),
            ("Alle bestanden", "*.*")
        ]
        files_selected = filedialog.askopenfilenames(title="Kies bestanden om te vertalen", filetypes=file_types)
        
        if files_selected:
            self.selected_files = [Path(f) for f in files_selected]
            
            # Update het invoerveld met een nette omschrijving
            self.entry_files.configure(state="normal")
            self.entry_files.delete(0, 'end')
            if len(self.selected_files) == 1:
                self.entry_files.insert(0, self.selected_files[0].name)
            else:
                self.entry_files.insert(0, f"{len(self.selected_files)} bestanden geselecteerd")
            self.entry_files.configure(state="readonly")
            
            self.log(f"📁 {len(self.selected_files)} bestand(en) geselecteerd voor vertaling.")

    def log(self, message: str):
        def append():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", message + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self.root.after(0, append)

    def on_start_click(self):
        if not self.selected_files:
            self.show_error("Geen bestanden", "Selecteer a.u.b. eerst één of meerdere bestanden via de 'Selecteer...' knop.")
            return
        self.start_callback(self.selected_files)

    def set_busy(self, busy: bool):
        if busy:
            self.btn_start.configure(state="disabled", text="Bezig...")
        else:
            self.btn_start.configure(state="normal", text="Start Vertaling")

    def update_progress(self, current: int, total: int):
        if total > 0:
            self.progress_bar.set(current / total)

    def show_error(self, title, msg):
        messagebox.showerror(title, msg)

    def show_info(self, title, msg):
        messagebox.showinfo(title, msg)