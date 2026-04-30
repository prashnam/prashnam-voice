# prashnam-voice

Local English → Indian-language voice generator for [prashnam.ai](https://prashnam.ai).
Type your content in English, pick languages, get an MP3 per item per language.

**No third-party logins.** No Hugging Face account, no API keys, no
cloud round-trip. The translation + TTS models download once and run
on-device after that. Cloud (Sarvam.ai) is available as a side door if
you'd rather pay-per-call than load 5 GB of weights.

Three project shapes ship in the box:

- **Poll** — 1 question + N options, optional NOTA-locked rotations.
- **Announcement** — N free-form body segments.
- **IVR** — branching call flow: `prompt` / `menu` / `response` / `bridge` / `terminator` segments wired by DTMF edges, with a visual DAG editor and a walk simulator for end-to-end dry runs.

## Languages

23 in total: English + 22 Indic languages — every language where
IndicTrans2 (translation) and Indic Parler-TTS (audio) overlap. New
projects default to **English + Hindi**; the rest sit unchecked in
project settings for opt-in.

> en, hi, ta, te, bn, mr, kn, gu, ml, or, pa, as, ur, ne, sa, mai, ks,
> sd, brx, doi, kok, mni, sat

Voices: per-language pool from the Indic Parler-TTS model card (e.g.
Hindi has Rohit / Divya / Aman / Rani; English has 21 speakers); cloud
adapter (Sarvam) exposes 35 v3 voices that work cross-language.

## Stack

| | |
|---|---|
| Translation | [`naklitechie/indictrans2-en-indic-dist-200M`](https://huggingface.co/naklitechie/indictrans2-en-indic-dist-200M) — verbatim mirror of [`ai4bharat/…`](https://huggingface.co/ai4bharat/indictrans2-en-indic-dist-200M) (MIT) |
| TTS | [`naklitechie/indic-parler-tts`](https://huggingface.co/naklitechie/indic-parler-tts) — verbatim mirror of [`ai4bharat/…`](https://huggingface.co/ai4bharat/indic-parler-tts) (Apache-2.0) |
| Languages | en + 22 Indic (see above) |

## Install

Two prerequisites, then one command. No Terminal required on macOS or Windows
if you used the official python.org installer.

1. **[Install Python 3.11](https://www.python.org/downloads/release/python-3119/)** if you don't already have it. (3.13/3.14 lack ML wheels — pick 3.11.)
2. **Get the repo.** `git clone https://github.com/prashnam/prashnam-voice.git` — or "Download ZIP" + unzip.
3. **Run `install.py`** — double-click it in Finder/Explorer (macOS/Windows), or `python3 install.py` from a terminal.

That's it. The script creates a venv, installs dependencies, launches the
local server, and opens the setup page in your browser. Keep its window
open while you use the app — closing it stops the server.

The first regen downloads ~4.9 GB of model weights (one-time) from
public mirrors at [`huggingface.co/naklitechie/*`](https://huggingface.co/naklitechie) — no
account, no token, no licence-acceptance click-through. After that
everything runs offline.

### Daily launch

Run `install.py` again — it's the same script for first install and every
relaunch. When the venv is already set up, it skips pip and starts the
server in a couple of seconds. If port 8765 is busy it walks up to 8775
and the bootstrap page (`index.html`) auto-discovers the new port, so
deep-links still work.

Force a clean reinstall: `rm -rf .venv` and run `install.py` again.
Pin a specific port: `PRASHNAM_PORT=9000 python3 install.py`.

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

# bulk-create projects from a CSV (poll or announcement)
prashnam-voice batch examples/polls.csv --domain poll --langs hi,ta,bn,en
prashnam-voice batch examples/announcements.csv --domain announcement

# list default voices
prashnam-voice list-voices

# clear the audio cache
prashnam-voice cache-clear

# download model weights ahead of time (~4.9 GB)
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

- **Project list** (`#/`): create, open, delete projects, or **Import CSV…** to bulk-create polls/announcements from a single file (`group_id` column groups rows into projects — see `examples/polls.csv`, `examples/announcements.csv`). Each project lives on disk under `./projects/<id>/`.
- **Project editor** (`#/p/<id>`): edit settings (name, languages, default pace, per-language pace overrides, **pronunciation lexicon** for fixing how TTS says specific names/places — global plus per-language overrides — and **templates** that wrap each segment, e.g. polls default to `Question: {q}` / `Press {n} for {opt}`, opt-out per segment), and a list of segments (one question + N options).
  - Type English text in any segment — after a 5 s pause, the backend re-translates and regenerates audio in every selected language for that segment.
  - Each (segment, language) cell shows the translated text, an inline audio player, a `⟳` to regenerate just that one MP3, a "Voice / pace" disclosure to override the per-language defaults *for that one segment*, and a "Takes" disclosure to browse and switch between all previous attempts.
  - Each segment row has a `⟳ all` button to regenerate every selected language for that segment.
  - **Option-order rotations**: opt in from the editor toolbar to generate K shuffled orderings of options (NOTA pinned to the end). Each rotation gets its own audio per language; the zip export organizes by `r0/<lang>/...`, `r1/<lang>/...`.
  - "Download .zip" packs the current take per (segment, language) into a flat `<lang>/{question,option_N}.mp3` layout.

The topbar shows the active engine (`local-ai4bharat` or `sarvam`) as a
small pill — click it to re-run onboarding and switch engines without
editing config files.

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

### IVR projects (DAG editor)

Pick **IVR menu** in the new-project dialog and the editor switches to a
visual call-flow canvas. Five segment types — `prompt`, `menu`,
`response`, `bridge`, `terminator` — rendered as nodes; DTMF + special
edges (`1`–`9`, `0`, `*`, `#`, `timeout`, `invalid`) drawn between them.

- **Drag** a node to move it; positions persist.
- **Drag from a port** on the right of a node onto another node to wire
  an edge. A dropdown lets you pick the key (DTMF digit or special).
- **Click a node** to focus the segment editor in the right pane —
  English text, per-language translations, audio takes — exactly like
  poll/announcement segments.
- **Set as start** pins the call-flow's entry point.
- **▶ Walk** opens a dialog with a 12-key DTMF keypad + timeout/invalid
  chips. Plays the active node's audio in your chosen language; pressing
  a key follows the matching edge. Breadcrumbs show the path so far.
  Stops on a terminator or an unmapped edge.

The DAG topology is plain JSON on disk (`segments[].edges`,
`segments[].x` / `.y`, `start_segment_id`) — no separate graph file. See
[`docs/python-api.md`](docs/python-api.md) for the
`store.set_segment_edge` / `store.set_start_segment` /
`project.resolve_start_segment` API surface, and
[`docs/rest-api.md`](docs/rest-api.md) for the four IVR HTTP endpoints
(`/api/ivr-keys`, `PATCH …/edge`, `PATCH …/position`,
`PATCH …/start-segment`).

## One-shot CLI (no project, no UI)

The `generate` subcommand from the CLI section above writes to a flat
`<lang>/{question,option_N}.mp3` layout under `output/<run_id>/`,
alongside a `translations.json` and a `meta.json` (model versions,
timings, cache hits). `<run_id>` is a local timestamp
(`YYYYMMDD_HHMMSS`). Useful for scripted batch runs that don't need the
project history / takes / rotations machinery.

```bash
prashnam-voice generate \
  --question "Who will win the election?" \
  --option "Party A" --option "Party B" --option "Party C" \
  --langs hi,ta,bn \
  --pace slow --pace ta=very_slow \
  --out ./output
```

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

- **First-run download ≈ 4.9 GB** to `~/.cache/huggingface/`. Use `prashnam-voice prefetch` to do this up front.
- **Mirrored weights**: we pull from public ungated mirrors at [`naklitechie/*`](https://huggingface.co/naklitechie). Bytes are byte-identical to the upstream AI4Bharat repos; the redistribution is permitted by the upstream licences (MIT + Apache-2.0). Each mirror's `NOTICE.md` documents provenance and citation back to AI4Bharat — please cite them, not us, in any research write-up.
- **Punjabi (pa)** is listed as "unofficial" in Indic Parler-TTS; pronunciation is weaker than the other 9. Acceptable for v1; revisit with IndicF5 if quality is unusable.
- **Apple Silicon**: TTS runs on `mps` if available with a startup probe; falls back to CPU automatically if MPS errors. Translation uses `mps` in fp16.
- **Voice names**: defaults are picked from Parler-TTS recommended speakers; override per-call with `--voice hi=Aditi`.
