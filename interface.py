import customtkinter as ctk
from tkinter import filedialog, messagebox
from pathlib import Path

# Thema instellingen (Opties: "System", "Dark", "Light")
ctk.set_appearance_mode("System")  
# Kleurenschema (Opties: "blue", "green", "dark-blue")
ctk.set_default_color_theme("blue") 

class TranslatorUI:
    def __init__(self, root, start_callback):
        self.root = root
        self.start_callback = start_callback
        
        self.root.title("File Translator")
        self.root.geometry("700x550")
        self.root.minsize(600, 450)

        self.default_input = Path.cwd() / "files_input"
        self.default_output = Path.cwd() / "files_translated"
        self.default_input.mkdir(exist_ok=True)
        self.default_output.mkdir(exist_ok=True)

        self.setup_ui()
        
    def setup_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1) # Log-frame schuift een rij op naar rij 3

        # ── API KEY FRAME ──
        frame_key = ctk.CTkFrame(self.root, corner_radius=10)
        frame_key.grid(row=0, column=0, padx=15, pady=10, sticky="ew")
        frame_key.columnconfigure(1, weight=1)

        lbl_key_title = ctk.CTkLabel(frame_key, text="API Instellingen", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_key_title.grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=5)

        ctk.CTkLabel(frame_key, text="API Key:").grid(row=1, column=0, sticky="w", padx=15, pady=5)
        self.entry_key = ctk.CTkEntry(frame_key, placeholder_text="Plak hier je API sleutel...", show="*")
        self.entry_key.grid(row=1, column=1, sticky="ew", padx=15, pady=5)

        # ── MAP SELECTIE FRAME (Schuift naar rij 1) ──
        frame_dirs = ctk.CTkFrame(self.root, corner_radius=10)
        frame_dirs.grid(row=1, column=0, padx=15, pady=10, sticky="ew")
        frame_dirs.columnconfigure(1, weight=1)

        lbl_title = ctk.CTkLabel(frame_dirs, text="Mappen Selecteren", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_title.grid(row=0, column=0, columnspan=3, sticky="w", padx=15, pady=5)

        # Input Map
        ctk.CTkLabel(frame_dirs, text="Input map:").grid(row=1, column=0, sticky="w", padx=15, pady=5)
        self.entry_input = ctk.CTkEntry(frame_dirs, placeholder_text="Selecteer invoermap...")
        self.entry_input.insert(0, str(self.default_input))
        self.entry_input.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        ctk.CTkButton(frame_dirs, text="Bladeren...", width=100, command=self.browse_input).grid(row=1, column=2, padx=15, pady=5)

        # Output Map
        ctk.CTkLabel(frame_dirs, text="Output map:").grid(row=2, column=0, sticky="w", padx=15, pady=5)
        self.entry_output = ctk.CTkEntry(frame_dirs, placeholder_text="Selecteer uitvoermap...")
        self.entry_output.insert(0, str(self.default_output))
        self.entry_output.grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        ctk.CTkButton(frame_dirs, text="Bladeren...", width=100, command=self.browse_output).grid(row=2, column=2, padx=15, pady=5)

        # ── ACTIE FRAME (Schuift naar rij 2) ──
        frame_actions = ctk.CTkFrame(self.root, fg_color="transparent")
        frame_actions.grid(row=2, column=0, padx=15, pady=5, sticky="ew")
        frame_actions.columnconfigure(1, weight=1)
        
        self.btn_start = ctk.CTkButton(frame_actions, text="Start Vertaling", font=ctk.CTkFont(weight="bold"), command=self.on_start_click)
        self.btn_start.grid(row=0, column=0, padx=5, pady=5)
        
        self.progress_bar = ctk.CTkProgressBar(frame_actions)
        self.progress_bar.grid(row=0, column=1, sticky="ew", padx=15, pady=5)
        self.progress_bar.set(0)

        # ── LOG FRAME (Schuift naar rij 3) ──
        frame_log = ctk.CTkFrame(self.root, corner_radius=10)
        frame_log.grid(row=3, column=0, padx=15, pady=10, sticky="nsew")
        frame_log.columnconfigure(0, weight=1)
        frame_log.rowconfigure(1, weight=1)

        lbl_log_title = ctk.CTkLabel(frame_log, text="Voortgang & Log", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_log_title.grid(row=0, column=0, sticky="w", padx=15, pady=5)

        self.txt_log = ctk.CTkTextbox(frame_log, font=("Consolas", 12))
        self.txt_log.grid(row=1, column=0, sticky="nsew", padx=15, pady=10)
        self.txt_log.configure(state="disabled")

        self.log("Applicatie opgestart. Vul je API-key in, selecteer mappen en klik op 'Start Vertaling'.")

    def browse_input(self):
        dir_selected = filedialog.askdirectory(initialdir=self.entry_input.get())
        if dir_selected:
            self.entry_input.delete(0, 'end')
            self.entry_input.insert(0, dir_selected)

    def browse_output(self):
        dir_selected = filedialog.askdirectory(initialdir=self.entry_output.get())
        if dir_selected:
            self.entry_output.delete(0, 'end')
            self.entry_output.insert(0, dir_selected)

    def log(self, message: str):
        def append():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", message + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self.root.after(0, append)

    def on_start_click(self):
        input_dir = Path(self.entry_input.get())
        output_dir = Path(self.entry_output.get())
        self.start_callback(input_dir, output_dir)

    def set_busy(self, busy: bool):
        if busy:
            self.btn_start.configure(state="disabled", text="Bezig...")
        else:
            self.btn_start.configure(state="normal", text="Start Vertaling")

    def update_progress(self, current: int, total: int):
        if total > 0:
            # CustomTkinter verwacht een float-waarde tussen 0.0 en 1.0
            self.progress_bar.set(current / total)

    def show_error(self, title, msg):
        messagebox.showerror(title, msg)

    def show_info(self, title, msg):
        messagebox.showinfo(title, msg)