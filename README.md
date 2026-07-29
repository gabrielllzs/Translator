# File Translator

A Windows desktop app that translates scanned documents and images into plain text using Google's Gemini API.

It is built for official and legal paperwork — the kind of document that arrives as a scan or a photo rather than as editable text. Gemini reads everything on the page (including stamps, handwriting and margin notes) and returns a faithful translation, which the app saves as a `.txt` file.

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Flet](https://img.shields.io/badge/ui-Flet-blueviolet) ![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Batch translation** — select any number of files and translate them in one run.
- **OCR built in** — no separate OCR step. Gemini reads directly from PDFs and photos, including stamps and handwritten text.
- **Large-PDF handling** — documents over 20 pages are split into chunks, translated separately and stitched back together, so long files don't hit the model's token limits.
- **Stop button** — cancel a running batch; it stops cleanly after the current file or chunk.
- **Live progress log** — per-file status, a progress bar, and timings for the upload and translation steps.
- **Secure key storage** — your API key is stored in the Windows Credential Manager, never in a plaintext file.
- **Automatic updates** — the app checks GitHub on startup and can download and run the new installer for you.
- **Light/dark theme** — follows your Windows appearance setting.

## Supported input formats

`.pdf` · `.jpg` · `.jpeg` · `.png` · `.webp` · `.heic` · `.tiff` · `.tif`

## Target languages

Dutch (`Nederlands`), Arabic, English. The app's own interface is in Dutch.

---

## Installation

### For users

Download and run `TranslatorInstaller.exe` from the [latest release](https://github.com/gabrielllzs/Translator/releases/latest). The installer fetches the application files and installs to `%APPDATA%\Translator` — no administrator rights required.

### For developers

```bash
git clone https://github.com/gabrielllzs/Translator.git
cd Translator
pip install -r requirements.txt
python main.py
```

Requires Python 3.10 or newer (both `flet` and `google-genai` need 3.10+; developed on 3.11).

---

## Getting an API key

1. Create a key at [Google AI Studio](https://aistudio.google.com/apikey).
2. Launch the app, click the **⚙ gear icon**, paste the key, and press **Opslaan**.

The key is written to the Windows Credential Manager under service `TranslatorApp`, and is loaded automatically on every subsequent launch.

For development you can instead put it in a `.env` file next to `main.py`:

```env
GEMINI_API_KEY=your_key_here
```

`GOOGLE_API_KEY` also works. A key typed into the settings dialog takes precedence over `.env`. **`.env` is gitignored — never commit your key.**

---

## Usage

1. Pick a **target language** from the dropdown.
2. Click **Selecteren...** and choose one or more files.
3. Click **Start Vertaling**.

Translations are written next to your input files, in a folder named `bestanden_vertaald`, as `<original-name>_<language>.txt`. For example, selecting `C:\scans\deed.pdf` with Dutch as the target produces:

```
C:\scans\bestanden_vertaald\deed_Nederlands.txt
```

Use **Stop** to cancel. Because an in-flight API request cannot be aborted cheaply, cancellation takes effect after the current file or PDF chunk finishes — already-completed files are kept.

---

## How it works

Three modules, cleanly separated so the UI and the translation logic don't depend on each other:

| File | Responsibility |
| --- | --- |
| [`main.py`](main.py) | `MainController` — wires UI to engine, manages the API key, runs the translation on a background thread, and handles update checks. |
| [`interface.py`](interface.py) | `TranslatorUI` — the Flet UI: theme, layout, dialogs, file picker, and the log/progress panel. |
| [`engine.py`](engine.py) | `GeminiTranslator` — uploads files to Gemini, splits large PDFs, sends the translation prompt, and writes the output. |

### The translation pipeline

For each selected file:

1. **Upload** the file to the Gemini Files API and poll until its state is `ACTIVE` (up to 120 s).
2. **Chunk if needed** — PDFs longer than `PAGES_PER_CHUNK` (20) are split with `pypdf` into temporary part-files, each translated in turn and then joined with blank lines between parts. A failure in any chunk aborts that file.
3. **Translate** via `generate_content` using the model `gemini-3.1-flash-lite`, with a system prompt that enforces the rules that matter for official documents: output only in the target language, no preamble or meta-commentary, no invented facts, numbers and dates preserved exactly, nothing omitted.
4. **Write** the result as UTF-8 text and **delete** the uploaded file from Gemini.

PDF pages are sent at `MEDIA_RESOLUTION_MEDIUM`. Gemini 3 defaults to a higher per-page resolution that can blow past the input token limit on long documents, and medium is the point where OCR quality saturates for documents.

### Threading model

Translation runs on a daemon thread so the window stays responsive. Every UI update from that thread is marshalled onto Flet's event loop via `page.run_task()`.

This matters: Flet delivers updates through an `asyncio.Queue.put_nowait()`, which is not thread-safe and **does not wake a sleeping event loop**. Calling `control.update()` directly from a worker thread leaves updates queued until the loop wakes for some other reason — measured at over 5 seconds of lag, which makes the log and progress bar appear frozen. `page.run_task()` uses `run_coroutine_threadsafe`, which wakes the loop immediately.

The log is capped at 500 lines, since each `update()` diffs the whole list and an unbounded log makes every subsequent line more expensive.

---

## Configuration

Values worth knowing, all near the top of their file:

| Setting | Location | Default |
| --- | --- | --- |
| Model | [`engine.py`](engine.py) `_DEFAULT_MODEL` | `gemini-3.1-flash-lite` |
| Pages per PDF chunk | [`engine.py`](engine.py) `PAGES_PER_CHUNK` | `20` |
| Max output tokens | [`engine.py`](engine.py) | `65536` |
| Thinking level | [`engine.py`](engine.py) | `low` |
| Target languages | [`interface.py`](interface.py) dropdown options | Nederlands, Arabic, English |
| Log line cap | [`interface.py`](interface.py) `MAX_LOG_LINES` | `500` |
| Colour palette | [`interface.py`](interface.py) `LIGHT_SCHEME` / `DARK_SCHEME` | slate + indigo |

To add a language, add an `ft.DropdownOption` — the value is passed straight into the prompt, so any language Gemini knows will work.

---

## Building a release

The app is packaged with [`flet pack`](https://flet.dev/docs/publish/windows) (PyInstaller under the hood) and wrapped in an [Inno Setup](https://jrsoftware.org/isinfo.php) installer.

Release steps, **in this order** — the order matters, see the warnings below:

1. **Bump the version in the code first**, because `CURRENT_VERSION` is compiled into the executable:
   - `CURRENT_VERSION` in [`main.py`](main.py)
   - `AppVersion` in [`Translator_Installer.iss`](Translator_Installer.iss)
2. **Build** the one-folder bundle:
   ```bash
   flet pack main.py -D -y -n main --product-name "File Translator" --product-version <version>
   ```
   `-D` produces `dist/main/main.exe`, which is the layout the installer expects.
3. **Zip the *contents* of `dist/main`** as `build.zip` — `main.exe` must sit at the root of the archive, not inside a `main/` folder, because the installer expands it straight into `{app}` and launches `{app}\main.exe`.
4. **Compile** `Translator_Installer.iss` with Inno Setup (`ISCC.exe`) to produce `TranslatorInstaller.exe`.
5. **Create a GitHub Release** and attach **both** `build.zip` and `TranslatorInstaller.exe`. The installer downloads `build.zip` from `releases/latest` at install time, which keeps the installer itself small.
6. **Last of all**, bump `version` in [`version.json`](version.json) and push it.

> ⚠️ **Bump the code version before building.** `CURRENT_VERSION` is baked into `main.exe`. If you build first and bump afterwards, the shipped app still reports the old version, so it keeps seeing itself as out of date and prompts on every launch.

> ⚠️ **Bump `version.json` last.** Installed clients poll it on startup and prompt to update as soon as it exceeds their own version. If the release artifacts aren't live yet, users download the old installer, land back on the same version, and get prompted again every launch.

### How updating works

On startup the app fetches [`version.json`](version.json) from `main`, compares it against `CURRENT_VERSION` as parsed integer tuples (so `1.10.0 > 1.9.0` behaves correctly), and if a newer version exists offers to download `installer_url` to the temp folder and run it.

---

## Project layout

```
├── main.py                    # controller + entry point
├── interface.py               # Flet UI
├── engine.py                  # Gemini translation engine
├── requirements.txt
└── version.json               # polled by installed clients for updates
```

Two build files live outside version control:

- `main.spec` is generated by `flet pack` and is gitignored — don't hand-edit it, and don't build with `pyinstaller main.spec` (it references a temp version file that `flet pack` deletes afterwards). Always build via `flet pack`.
- `Translator_Installer.iss` is currently matched by the `*.iss` rule in `.gitignore`, so the installer script is **not** in the repository. Keep a local copy safe, or un-ignore it so releases are reproducible from a clean clone.

## Known limitations

- **Windows only.** The key store uses the Windows Credential Manager and the updater runs an `.exe`.
- **No retry on API errors.** A transient failure fails that file; the rest of the batch continues.
- **The updater does not verify the installer.** It runs whatever `installer_url` serves over HTTPS, with no hash or signature check.
- **Cancellation is not immediate** — it waits for the current API call to finish.
- Output is plain text: layout, tables and images are not preserved.

## License

MIT — see [LICENSE](LICENSE).
