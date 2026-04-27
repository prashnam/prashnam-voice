# prashnam-voice

Local English → Indian-language voice poll generator for [prashnam.ai](https://prashnam.ai).
Type a poll question and options in English, pick languages, get an MP3 per item per language. Fully offline after first-run model download. No API keys.

## Stack

| | |
|---|---|
| Translation | [`ai4bharat/indictrans2-en-indic-dist-200M`](https://huggingface.co/ai4bharat/indictrans2-en-indic-dist-200M) |
| TTS | [`ai4bharat/indic-parler-tts`](https://huggingface.co/ai4bharat/indic-parler-tts) |
| Languages | hi, ta, te, bn, mr, kn, gu, pa, ml, or |

## Install

Two clicks. No Terminal required on macOS or Windows (assuming you used the
official Python installer).

1. **Get the repo.** `git clone …` (or "Download ZIP" → unzip).
2. **Open `index.html`** in your browser by double-clicking it in
   Finder/Explorer. It's a one-page setup guide that auto-detects when the
   server is running.
3. **Install Python 3.11+** if you don't already have it
   (https://www.python.org/downloads/).
4. **Run `install.py`:**
   - macOS — double-click `install.py`. Python Launcher (installed by
     python.org) opens it in Terminal.
   - Windows — double-click `install.py`. The Python Launcher (`py.exe`)
     runs it in a console window.
   - Linux (or anywhere via terminal) — `python3 install.py` from this
     folder.
5. The installer creates a virtual environment, installs dependencies, and
   launches the local server. The bootstrap page detects it and shows the
   "Open app" button. Keep the installer's window open while you use the
   app — closing it stops the server.

### Daily launch (after restart, closed terminal, etc.)

Run `install.py` again. The script is idempotent:

- If the venv already exists and dependencies are up-to-date (its
  `egg-info` mtime ≥ `pyproject.toml` mtime), the slow pip step is
  skipped and the server starts in a couple of seconds.
- If port `8765` is busy (an old instance, or another service), it
  walks up to `8775` and uses the first free one. The bootstrap page
  (`index.html`) probes the same range, so deep-links keep working.
- To force a clean reinstall: `rm -rf .venv` and run `install.py`.

Pinning a specific port: `PRASHNAM_PORT=9000 python3 install.py`.

### Manual install (terminal)

If your system doesn't run `.py` files on double-click, or you'd just rather
do it by hand:

```bash
brew install ffmpeg     # macOS — pydub needs the ffmpeg binary
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
prashnam-voice serve
```

## CLI

```bash
# generate one poll
prashnam-voice generate \
  --question "Who will win the election?" \
  --option "Party A" --option "Party B" --option "Party C" \
  --langs hi,ta,bn

# from a JSON file
prashnam-voice generate --from-json poll.json --langs all

# list default voices
prashnam-voice list-voices

# clear the audio cache
prashnam-voice cache-clear

# download model weights ahead of time (~4.5 GB)
prashnam-voice prefetch

# launch the web app
prashnam-voice serve --port 8765
```

`poll.json` schema:

```json
{ "question": "Who will win the election?", "options": ["Party A", "Party B", "Party C"] }
```

## Web app — project workflow

```bash
prashnam-voice serve            # default: ./projects + ./output
open http://127.0.0.1:8765
```

The web app is project-centric:

- **Project list** (`#/`): create, open, delete projects. Each project lives on disk under `./projects/<id>/`.
- **Project editor** (`#/p/<id>`): edit settings (name, languages, default pace, per-language pace overrides), and a list of segments (one question + N options).
  - Type English text in any segment — after a 700 ms pause, the backend re-translates and regenerates audio in every selected language for that segment.
  - Each (segment, language) cell shows the translated text, an inline audio player, a `⟳` to regenerate just that one MP3, and a "Takes" disclosure to browse and switch between all previous attempts.
  - Each segment row has a `⟳ all` button to regenerate every selected language for that segment.
  - "Download .zip" packs the current take per (segment, language) into a flat `<lang>/{question,option_N}.mp3` layout.

### On-disk layout (per project)

```
projects/<project_id>/
├── project.json                # name, langs, paces, segments[]
└── audio/<segment_id>/<lang>/
    ├── <attempt_id>.mp3
    └── <attempt_id>.json       # voice, pace, source_text, duration, timestamp
```

`project.json` is the source of truth — every attempt MP3 is kept (history); `current_takes` in the JSON is just a pointer per (segment, language).

### Manage projects from the CLI

```bash
prashnam-voice projects list
prashnam-voice projects create "Election 2026" --langs hi,ta,bn
prashnam-voice projects show <project-id>
prashnam-voice projects delete <project-id> -y
```

## Legacy one-shot CLI (still supported)

```bash
prashnam-voice generate \
  --question "Who will win the election?" \
  --option "Party A" --option "Party B" --option "Party C" \
  --langs hi,ta,bn \
  --pace slow --pace ta=very_slow \
  --out ./output
```

Output layout for one-shot runs:

```
output/<run_id>/
├── translations.json
├── meta.json
└── <lang>/
    ├── question.mp3
    ├── option_1.mp3
    └── ...
```

`<run_id>` is a local timestamp (`YYYYMMDD_HHMMSS`).

## Caching

Audio is content-addressed in `~/.cache/prashnam-voice/audio/<sha256>.mp3` keyed on `(model, lang, voice, text)`. Re-running the same poll re-uses cached MP3s instantly via hardlink.

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/rest-api.md`](docs/rest-api.md) | The HTTP API exposed by the running server — every endpoint with request/response shapes, status codes, and the two-queue job model. |
| [`docs/python-api.md`](docs/python-api.md) | Embedding `prashnam_voice` in your own Python code — projects, pipeline, swapping engines, registering custom adapters or domains, CSV import. |
| [`PLAN.md`](PLAN.md) | Tier 1 + Tier 2 milestones, status, and design decisions. |
| [`guide/README.md`](guide/README.md) | Visual tour of the web app (screenshots of every major view). |

## Notes / known quirks

- **First-run download ≈ 4.5 GB** to `~/.cache/huggingface/`. Use `prashnam-voice prefetch` to do this up front.
- **Punjabi (pa)** is listed as "unofficial" in Indic Parler-TTS; pronunciation is weaker than the other 9. Acceptable for v1; revisit with IndicF5 if quality is unusable.
- **Apple Silicon**: TTS runs on `mps` if available with a startup probe; falls back to CPU automatically if MPS errors. Translation uses `mps` in fp16.
- **Voice names**: defaults are picked from Parler-TTS recommended speakers; override per-call with `--voice hi=Aditi`.
