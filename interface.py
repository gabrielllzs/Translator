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

        self.selected_files = []

        self.root.title("File Translator")
        self.root.geometry("760x580")
        self.root.minsize(640, 480)

        self.setup_ui()

    # ------------------------------------------------------------------
    # Kleine helper om een sectiekopje te tekenen (label in kapitalen).
    # Vervangt de losse omkaderde frames door een lichtere structuur.
    # ------------------------------------------------------------------
    def _section_label(self, parent, text, row, pady_top=18):
        lbl = ctk.CTkLabel(
            parent, text=text.upper(),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray40", "gray65"),
        )
        lbl.grid(row=row, column=0, columnspan=3, sticky="w", pady=(pady_top, 6))

    def _divider(self, parent, row, pady=16):
        line = ctk.CTkFrame(parent, height=1, fg_color=("gray80", "gray30"), corner_radius=0)
        line.grid(row=row, column=0, columnspan=3, sticky="ew", pady=pady)

    # ------------------------------------------------------------------
    def setup_ui(self):
        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew", padx=28, pady=22)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)

        # ── Header ──
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        header.columnconfigure(0, weight=1)

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            title_block, text="File Translator",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkButton(
            header, text="⚙️", width=36, height=36,
            fg_color="transparent", hover_color=("gray85", "gray25"),
            text_color=("gray30", "gray70"),
            font=ctk.CTkFont(size=15),
            command=self.open_settings,
        ).grid(row=0, column=1, sticky="e")

        self._divider(content, row=1, pady=(18, 4))

        # ── Doeltaal ──
        self._section_label(content, "Doeltaal", row=2)
        self.lang_var = ctk.StringVar(value="Nederlands")
        self.lang_menu = ctk.CTkOptionMenu(
            content, variable=self.lang_var, values=["Nederlands", "Arabic", "English"],
            height=36, width=200,
        )
        self.lang_menu.grid(row=3, column=0, sticky="w")

        # ── Documenten ──
        self._section_label(content, "Documenten", row=4)
        self.entry_files = ctk.CTkEntry(
            content, placeholder_text="Kies één of meerdere bestanden...", height=36,
        )
        self.entry_files.grid(row=5, column=0, columnspan=2, sticky="ew", padx=(0, 10))
        self.entry_files.configure(state="readonly")
        ctk.CTkButton(
            content, text="Selecteren...", width=110, height=36, command=self.browse_files,
        ).grid(row=5, column=2, sticky="e")

        self._divider(content, row=6, pady=16)

        # ── Actie: start + voortgang ──
        action_row = ctk.CTkFrame(content, fg_color="transparent")
        action_row.grid(row=7, column=0, columnspan=3, sticky="ew")
        action_row.columnconfigure(1, weight=1)

        self.btn_start = ctk.CTkButton(
            action_row, text="Start Vertaling", width=150, height=38,
            font=ctk.CTkFont(weight="bold"),
            command=self.on_start_click,
        )
        self.btn_start.grid(row=0, column=0, padx=(0, 16))

        self.progress_bar = ctk.CTkProgressBar(action_row, height=8)
        self.progress_bar.grid(row=0, column=1, sticky="ew")
        self.progress_bar.set(0)

        # ── Log ──
        self._section_label(content, "Voortgang & log", row=8, pady_top=22)

        log_wrap = ctk.CTkFrame(content, corner_radius=8)
        log_wrap.grid(row=9, column=0, columnspan=3, sticky="nsew")
        log_wrap.columnconfigure(0, weight=1)
        log_wrap.rowconfigure(0, weight=1)
        content.rowconfigure(9, weight=1)

        self.txt_log = ctk.CTkTextbox(log_wrap, font=("Consolas", 12), fg_color="transparent")
        self.txt_log.grid(row=0, column=0, sticky="nsew", padx=12, pady=10)
        self.txt_log.configure(state="disabled")

        self._build_settings_window()

        self.log("Applicatie opgestart. Selecteer je bestanden en klik op 'Start Vertaling'.")

    # ------------------------------------------------------------------
    # Instellingen-modal (bevat de API-sleutel). Wordt bij opstarten
    # meteen aangemaakt maar verborgen, zodat entry_key altijd bestaat —
    # main.py vult 'm namelijk direct bij het opstarten van de app.
    # ------------------------------------------------------------------
    def _build_settings_window(self):
        self.settings_window = ctk.CTkToplevel(self.root)
        self.settings_window.title("Instellingen")
        self.settings_window.geometry("420x220")
        self.settings_window.resizable(False, False)
        # Aan het hoofdvenster gekoppeld houden (blijft er altijd boven op)
        self.settings_window.transient(self.root)
        # Sluiten via het kruisje verbergt het venster i.p.v. het te vernietigen —
        # entry_key moet als widget blijven bestaan.
        self.settings_window.protocol("WM_DELETE_WINDOW", self.close_settings)

        wrapper = ctk.CTkFrame(self.settings_window, fg_color="transparent")
        wrapper.pack(fill="both", expand=True, padx=24, pady=22)

        ctk.CTkLabel(
            wrapper, text="Instellingen", font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            wrapper, text="API-SLEUTEL", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray40", "gray65"),
        ).pack(anchor="w", pady=(18, 6))

        self.entry_key = ctk.CTkEntry(
            wrapper, placeholder_text="Plak hier je API-sleutel...", show="*", height=36,
        )
        self.entry_key.pack(fill="x", pady=(0, 16))

        btn_row = ctk.CTkFrame(wrapper, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(
            btn_row, text="Opslaan", height=36, command=self.on_save_key_click,
        ).pack(side="left", expand=True, fill="x", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Sluiten", height=36, fg_color="transparent",
            hover_color=("gray85", "gray25"), text_color=("gray20", "gray85"),
            border_width=1, command=self.close_settings,
        ).pack(side="left", expand=True, fill="x")

        # Verberg meteen — dit venster bestaat alleen zodat entry_key
        # als widget beschikbaar is; het wordt pas getoond via het ⚙-icoon.
        self.settings_window.withdraw()

    def open_settings(self):
        self.settings_window.deiconify()
        self.root.update_idletasks()
        
        # Centreer het instellingenvenster op het hoofdvenster
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 210
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 110
        self.settings_window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        
        self.settings_window.lift()
        self.settings_window.focus_force()
        
        # Modaal gedrag activeren
        self.settings_window.grab_set()
        
        # BLOKKEER het hoofdvenster (Main loop wacht hier totdat het verborgen/gesloten is)
        self.root.wait_window(self.settings_window)

    def close_settings(self):
        self.settings_window.grab_release()
        self.settings_window.withdraw()

    # ------------------------------------------------------------------
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
            self.show_error("Geen bestanden", "Selecteer a.u.b. eerst één of meerdere bestanden via de 'Selecteren...' knop.")
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