# prashnam-voice

Local English → Indian-language voice generator for [prashnam.ai](https://prashnam.ai). Type your content in English, pick from 23 languages (English + 22 Indic), get an MP3 per item per language. Runs on-device after a one-time ~4.9 GB model download — no Hugging Face account, no API keys.

Three project shapes: **Poll** (1 question + N options), **Announcement** (flat body segments), **IVR menu** (branching call flow with a DAG editor + walk simulator).

Highlights:

- **Translate + synthesize per segment.** Type English; auto-translates and synthesizes audio in every selected language. Multiple takes per cell, plus a hand-edit escape hatch on the translation. Generate one segment at a time or hit **Generate all**.
- **Merge for IVR (polls).** Concatenate the question + every option into one MP3 per language — configurable gap, optional end-of-prompt beep, per-language gain slider.
- **Option-order rotations.** Generate multiple shuffled orderings of poll options to neutralize primacy / recency bias; pin "None of the above" to the last slot.
- **Pronunciation lexicon.** `BJP=bee jay pee` per line, global or per-language — fixes proper nouns once per project.

## Install

1. **[Install Python 3.11](https://www.python.org/downloads/release/python-3119/)** if you don't already have it. (3.13 / 3.14 lack the ML wheels — pick 3.11.)
2. **Get the repo:** `git clone https://github.com/prashnam/prashnam-voice.git`
3. **Run `install.py`** — double-click it in Finder/Explorer (macOS/Windows), or `python3 install.py` from a terminal.

The script creates a venv, installs dependencies, launches the local server at `http://127.0.0.1:8765`, and opens the browser — to the setup wizard on first run, to the editor afterwards. Re-run any time; it doubles as the daily launcher and skips the slow pip step once `.venv/` exists. Model weights download once from public mirrors at [`huggingface.co/naklitechie/*`](https://huggingface.co/naklitechie); everything runs offline after that.

If port 8765 is busy the launcher walks up to 8775. Force a clean reinstall with `rm -rf .venv && python3 install.py`.

### Manual install (terminal)

```bash
brew install ffmpeg                # macOS — pydub needs the ffmpeg binary
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
prashnam-voice serve
```

## Upgrade

```bash
git pull
python3 install.py                 # picks up new deps and starts the server
```

Or, if you installed manually:

```bash
git pull
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
prashnam-voice serve
```

Existing projects under `./projects/` keep working — `project.json` is forward-compatible (new fields default sensibly on load). The synth cache at `~/.cache/prashnam-voice/audio/` is content-addressed and includes a post-process version, so recipe changes (like a loudness-normalization fix) invalidate stale entries automatically; run `prashnam-voice cache-clear` to reclaim the disk.

## Learn more

- **[Visual guide](guide/index.html)** — annotated walkthrough of every view. Open in a browser, or visit `/guide` while the server is running.
- **[REST API](docs/rest-api.md)**, **[Python embedding API](docs/python-api.md)** — for scripting against the running server.
- **[PLAN.md](PLAN.md)** — roadmap and design decisions.
- `prashnam-voice --help` — every CLI command (`serve`, `generate`, `batch`, `prefetch`, `cache-clear`, `projects`).

## Credits

Translation: [IndicTrans2](https://huggingface.co/naklitechie/indictrans2-en-indic-dist-200M) (MIT). TTS: [Indic Parler-TTS](https://huggingface.co/naklitechie/indic-parler-tts) (Apache-2.0). Both are verbatim mirrors of [AI4Bharat](https://ai4bharat.iitm.ac.in/) models — cite AI4Bharat in any research write-up.
