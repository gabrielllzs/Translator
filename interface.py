import flet as ft
from pathlib import Path

# Het logvenster begrenzen: elke update() stuurt een diff van de hele
# ListView, dus een onbeperkt groeiend log maakt iedere volgende logregel
# duurder (O(n²) over een lange vertaling).
MAX_LOG_LINES = 500

# ── Palet: slate-oppervlakken met een gedempt indigo accent ──
# Alles wordt via kleurrollen (SURFACE, ON_SURFACE_VARIANT, PRIMARY, ...)
# gebruikt, zodat licht/donker automatisch meeschakelt met Windows.
LIGHT_SCHEME = ft.ColorScheme(
    primary="#4F46E5",
    on_primary="#FFFFFF",
    surface="#F8FAFC",
    surface_container_lowest="#FFFFFF",
    surface_container_highest="#F1F5F9",
    on_surface="#0F172A",
    on_surface_variant="#64748B",
    outline="#CBD5E1",
    outline_variant="#E2E8F0",
    error="#DC2626",
)

DARK_SCHEME = ft.ColorScheme(
    primary="#818CF8",
    on_primary="#1E1B4B",
    surface="#0F172A",
    surface_container_lowest="#1E293B",
    surface_container_highest="#243244",
    on_surface="#F1F5F9",
    on_surface_variant="#94A3B8",
    outline="#475569",
    outline_variant="#334155",
    error="#F87171",
)

CONTROL_HEIGHT = 44
RADIUS = 8
BTN_SHAPE = ft.RoundedRectangleBorder(radius=RADIUS)


class TranslatorUI:
    def __init__(self, page: ft.Page, start_callback, save_key_callback, stop_callback):
        self.page = page
        self.start_callback = start_callback
        self.save_key_callback = save_key_callback
        self.stop_callback = stop_callback

        self.selected_files = []

        page.title = "File Translator"
        page.theme_mode = ft.ThemeMode.SYSTEM
        page.theme = ft.Theme(color_scheme=LIGHT_SCHEME)
        page.dark_theme = ft.Theme(color_scheme=DARK_SCHEME)
        page.window.width = 820
        page.window.height = 660
        page.window.min_width = 680
        page.window.min_height = 520
        page.padding = 28

        self.setup_ui()

    # ------------------------------------------------------------------
    # Kleine bouwstenen voor een consistente opmaak
    # ------------------------------------------------------------------
    def _section_label(self, text):
        return ft.Text(
            text.upper(), size=11, weight=ft.FontWeight.W_700,
            color=ft.Colors.ON_SURFACE_VARIANT,
            style=ft.TextStyle(letter_spacing=0.8),
        )

    def _hairline(self):
        return ft.Container(height=1, bgcolor=ft.Colors.OUTLINE_VARIANT)

    def _card(self, content, expand=False):
        return ft.Container(
            content=content,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=10,
            padding=14,
            expand=expand,
        )

    # ------------------------------------------------------------------
    def setup_ui(self):
        page = self.page
        page.bgcolor = ft.Colors.SURFACE

        # ── Header ──
        header = ft.Row(
            controls=[
                ft.Column(
                    spacing=2,
                    controls=[
                        ft.Text("File Translator", size=20, weight=ft.FontWeight.W_600),
                        ft.Text(
                            "Documenten en scans vertalen naar platte tekst",
                            size=12, color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                ),
                ft.IconButton(
                    icon=ft.Icons.SETTINGS_OUTLINED, tooltip="Instellingen",
                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                    on_click=self.open_settings,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        # ── Doeltaal ──
        self.lang_dropdown = ft.Dropdown(
            value="Nederlands",
            width=240,
            height=CONTROL_HEIGHT,
            filled=True,
            fill_color=ft.Colors.SURFACE_CONTAINER_LOWEST,
            border_color=ft.Colors.OUTLINE_VARIANT,
            border_radius=RADIUS,
            options=[
                ft.DropdownOption("Nederlands"),
                ft.DropdownOption("Arabic"),
                ft.DropdownOption("English"),
            ],
        )

        # ── Documenten ──
        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)

        self.files_field = ft.TextField(
            hint_text="Nog geen bestanden gekozen",
            read_only=True,
            expand=True,
            height=CONTROL_HEIGHT,
            filled=True,
            fill_color=ft.Colors.SURFACE_CONTAINER_LOWEST,
            border_color=ft.Colors.OUTLINE_VARIANT,
            border_radius=RADIUS,
            prefix_icon=ft.Icons.DESCRIPTION_OUTLINED,
        )
        self.btn_browse = ft.OutlinedButton(
            content="Selecteren...",
            icon=ft.Icons.FOLDER_OPEN_OUTLINED,
            height=CONTROL_HEIGHT,
            style=ft.ButtonStyle(shape=BTN_SHAPE, side=ft.BorderSide(1, ft.Colors.OUTLINE)),
            on_click=self.browse_files,
        )

        # ── Actie: start/stop + voortgang ──
        self.btn_start = ft.FilledButton(
            content="Start Vertaling",
            icon=ft.Icons.PLAY_ARROW_ROUNDED,
            height=CONTROL_HEIGHT, width=170,
            style=ft.ButtonStyle(shape=BTN_SHAPE),
            on_click=self.on_start_click,
        )
        self.btn_stop = ft.OutlinedButton(
            content="Stop",
            icon=ft.Icons.STOP_ROUNDED,
            height=CONTROL_HEIGHT, width=110,
            disabled=True,
            style=ft.ButtonStyle(shape=BTN_SHAPE, side=ft.BorderSide(1, ft.Colors.OUTLINE)),
            on_click=self.on_stop_click,
        )
        self.progress_bar = ft.ProgressBar(
            value=0, expand=True, bar_height=6, border_radius=3,
            color=ft.Colors.PRIMARY, bgcolor=ft.Colors.OUTLINE_VARIANT,
        )

        # ── Log ──
        self.log_view = ft.ListView(expand=True, spacing=3, auto_scroll=True)

        # ── Instellingen-dialoog (API-sleutel) ──
        self.entry_key = ft.TextField(
            label="API-sleutel", password=True, can_reveal_password=True,
            hint_text="Plak hier je API-sleutel...",
            filled=True,
            fill_color=ft.Colors.SURFACE_CONTAINER_LOWEST,
            border_color=ft.Colors.OUTLINE_VARIANT,
            border_radius=RADIUS,
            prefix_icon=ft.Icons.KEY_OUTLINED,
        )
        self.settings_dialog = ft.AlertDialog(
            modal=True,
            shape=ft.RoundedRectangleBorder(radius=12),
            title=ft.Text("Instellingen", size=17, weight=ft.FontWeight.W_600),
            content=ft.Container(
                width=380,
                content=ft.Column(
                    tight=True, spacing=10,
                    controls=[
                        ft.Text(
                            "De sleutel wordt versleuteld opgeslagen in de Windows Kluis.",
                            size=12, color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        self.entry_key,
                    ],
                ),
            ),
            actions=[
                ft.TextButton("Sluiten", on_click=self.close_settings),
                ft.FilledButton(
                    "Opslaan", style=ft.ButtonStyle(shape=BTN_SHAPE),
                    on_click=self.on_save_key_click,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        page.add(
            ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    header,
                    ft.Container(height=18),
                    self._hairline(),
                    ft.Container(height=20),

                    self._section_label("Doeltaal"),
                    ft.Container(height=8),
                    self.lang_dropdown,
                    ft.Container(height=20),

                    self._section_label("Documenten"),
                    ft.Container(height=8),
                    ft.Row(spacing=10, controls=[self.files_field, self.btn_browse]),
                    ft.Container(height=22),

                    ft.Row(
                        spacing=12,
                        controls=[self.btn_start, self.btn_stop, self.progress_bar],
                    ),
                    ft.Container(height=22),

                    self._section_label("Voortgang & log"),
                    ft.Container(height=8),
                    self._card(self.log_view, expand=True),
                ],
            )
        )

        self.log("Applicatie opgestart. Selecteer je bestanden en klik op 'Start Vertaling'.")

    # ------------------------------------------------------------------
    @property
    def lang_var(self):
        class _V:
            def get(_self):
                return self.lang_dropdown.value
        return _V()

    # ------------------------------------------------------------------
    # Alle UI-wijzigingen lopen hierlangs. Flet stuurt updates via een
    # asyncio.Queue naar de client; die queue is niet thread-safe en wekt
    # de event loop niet op als je hem vanuit een gewone thread vult.
    # Vertalen gebeurt in een achtergrondthread, dus zonder deze marshalling
    # blijven logregels/voortgang hangen tot de loop om een andere reden
    # wakker wordt (= log loopt achter). run_task() gebruikt intern
    # run_coroutine_threadsafe en wekt de loop wél.
    # ------------------------------------------------------------------
    async def _ui_apply(self, fn):
        fn()

    def _ui(self, fn):
        try:
            self.page.run_task(self._ui_apply, fn)
        except Exception:
            # Loop nog niet beschikbaar (bijv. tijdens het opbouwen van de UI).
            fn()

    # ------------------------------------------------------------------
    def open_settings(self, e=None):
        self.page.show_dialog(self.settings_dialog)

    def close_settings(self, e=None):
        self.page.pop_dialog()

    def on_save_key_click(self, e=None):
        api_key = (self.entry_key.value or "").strip()
        self.save_key_callback(api_key)

    async def browse_files(self, e=None):
        files_selected = await self.file_picker.pick_files(
            dialog_title="Kies bestanden om te vertalen",
            allow_multiple=True,
            allowed_extensions=["pdf", "jpg", "jpeg", "png", "webp", "heic", "tiff", "tif"],
        )
        if files_selected:
            self.selected_files = [Path(f.path) for f in files_selected]

            if len(self.selected_files) == 1:
                self.files_field.value = self.selected_files[0].name
            else:
                self.files_field.value = f"{len(self.selected_files)} bestanden geselecteerd"
            self.files_field.update()

            self.log(f"📁 {len(self.selected_files)} bestand(en) geselecteerd voor vertaling.")

    def log(self, message: str):
        def apply():
            self.log_view.controls.append(
                ft.Text(
                    message, font_family="Consolas", size=12, selectable=True,
                    color=ft.Colors.ON_SURFACE,
                )
            )
            if len(self.log_view.controls) > MAX_LOG_LINES:
                del self.log_view.controls[:-MAX_LOG_LINES]
            self.log_view.update()

        self._ui(apply)

    def on_start_click(self, e=None):
        if not self.selected_files:
            self.show_error("Geen bestanden", "Selecteer a.u.b. eerst één of meerdere bestanden via de 'Selecteren...' knop.")
            return
        self.start_callback(self.selected_files)

    def on_stop_click(self, e=None):
        if self.stop_callback:
            self.stop_callback()
        self.btn_stop.disabled = True
        self.btn_stop.content = "Stoppen..."
        self.btn_stop.update()

    def set_busy(self, busy: bool):
        def apply():
            if busy:
                self.btn_start.disabled = True
                self.btn_start.content = "Bezig..."
                self.btn_stop.disabled = False
                self.btn_stop.content = "Stop"
            else:
                self.btn_start.disabled = False
                self.btn_start.content = "Start Vertaling"
                self.btn_stop.disabled = True
                self.btn_stop.content = "Stop"
            self.btn_start.update()
            self.btn_stop.update()

        self._ui(apply)

    def update_progress(self, current: int, total: int):
        if total <= 0:
            return

        def apply():
            self.progress_bar.value = current / total
            self.progress_bar.update()

        self._ui(apply)

    def _alert(self, title, msg, on_yes=None, on_no=None):
        def close(e=None):
            self.page.pop_dialog()

        actions = []
        if on_yes is not None:
            def yes_click(e):
                close()
                on_yes()

            def no_click(e):
                close()
                if on_no:
                    on_no()

            actions = [
                ft.TextButton("Nee", on_click=no_click),
                ft.ElevatedButton("Ja", on_click=yes_click),
            ]
        else:
            actions = [ft.ElevatedButton("OK", on_click=close)]

        dialog = ft.AlertDialog(modal=True, title=ft.Text(title), content=ft.Text(msg), actions=actions)
        self._ui(lambda: self.page.show_dialog(dialog))

    def show_error(self, title, msg):
        self._alert(title, msg)

    def show_info(self, title, msg):
        self._alert(title, msg)

    def ask_yes_no(self, title, msg, on_yes):
        self._alert(title, msg, on_yes=on_yes)
