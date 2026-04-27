# prashnam-voice — Tier 1 + Tier 2 plan

> Living document. Tier 3 (auth, multi-tenant, hosted SaaS) is **not in scope**.

## Status (last updated 2026-04-27)

**Tier 1 complete.** 101 tests passing. Web app + CLI + bootstrap + adapter layer +
two domains + lexicon + CSV import + public API + onboarding wizard with HF gating
all shipped. Next up: Tier 2 (IVR + telephony) — Tier 3 (auth/SaaS) remains out of scope.

| Milestone | Status |
|---|---|
| **M1.1 — Engine adapter layer** | ✅ done. `local-ai4bharat` + `sarvam` adapters both wired through `app_config`. New adapters can register at import time via `prashnam_voice.public.register_{translator,tts}`. |
| **English (en) as a language** | ✅ done. en→en is a translator passthrough on both adapters; ships first in the language list so new projects include it by default. |
| **Numeral normalization** | ✅ done. `num2words` converts digits to English words before translation (`press 1` → `press one`) so Indic TTS pronounces them correctly. |
| **Bootstrap distribution** | ✅ done. `index.html` (file://) + `install.py` (cross-platform) at repo root replaced PyInstaller. Bootstrap polls `/api/health` + auto-routes the user to `/onboarding` or `/` based on the `onboarded` flag. |
| **Onboarding wizard** | ✅ done. `/onboarding` route + 6-step rail UI; HF probe (token-valid vs ToS-not-accepted vs ready) + Sarvam probe; real screenshots embedded in the ToS + token steps; persistence to `~/.config/prashnam-voice/config.json`. |
| **M1.2 — Pronunciation lexicon** | ✅ done. Per-project `lexicon` with global + per-language entries; whole-word substitution (`\bKEY\b`); applied in `effective_text` before template wrap and numeral pass; settings panel UI; lexicon change invalidates all cached translations + takes. |
| **M1.3 — Domain packs (poll + announcement)** | ✅ done. New `prashnam_voice.domains` registry with `register/get/all_domains`. `poll` (1 question + N options, with Prashnam preamble defaults) and `announcement` (N body segments, no preamble). `Project.domain` field; new-project dialog has Type radio; editor's "+ Add" button + auto-seed adapt to active domain; server rejects out-of-domain segment types. |
| **M1.4 — Bulk CSV import** | ✅ done. `prashnam_voice/csv_import.py`; CLI `prashnam-voice batch <file>`; web UI "Import CSV…" modal; sample files in `examples/`. Schemas: poll (`group_id,type,english`), announcement (`group_id,english`); optional `name`/`langs` columns; per-row errors are non-fatal. |
| **M1.5 — Promoted Python API** | ✅ done. `prashnam_voice.public` with 41 curated re-exports + `__all__`. `docs/api.md` with embedding examples (quick start, granular ops, adapter swap, custom adapter, custom domain, CSV import, lexicon, paths). Public-API smoke tests. |
| **Help modal** | ✅ done. "?" button in topbar opens a wide modal: Quick start (8 steps), where things live, reset-onboarding tip, links to PLAN/api docs. |
| **JS popups → dialogs/snackbars** | ✅ done. Reusable `confirmDialog()` with `danger=true` styling for destructive actions; `toast(msg, kind)` for transient alerts. Zero native `alert()`/`confirm()` calls remain in the frontend. |
| **/guide folder** | ✅ done. 7 real PNG screenshots of the running app: project list, new-project dialog, import-csv dialog, project editor, settings panel, onboarding wizard, help modal. `guide/README.md` indexes them. |
| **Option-order rotations** | ✅ done. `Segment.lock_at_end` + `Project.rotation_count/seed/rotations` with auto-migration of legacy `{lang: str}` shapes. Per-rotation `effective_text` so `{n}` reflects the option's spot in each shuffle. `enable_rotations(lock_last_as_nota=...)` / `disable_rotations` / `reshuffle_rotations` mutators; full REST surface; project-page "Enable rotations" CTA + NOTA confirm dialog; per-option 🔒 lock pill; cells stack one audio row per rotation; zip export reorganized as `r0/...`, `r1/...`. |
| **Documentation** | ✅ done. `docs/rest-api.md` (every HTTP endpoint with body/response shapes + status codes + the two-queue model), `docs/python-api.md` (embedding the library). |
| **Phase 3 — Model download with progress UI** | ✅ done. `POST /api/onboarding/download-models` kicks off `huggingface_hub.snapshot_download` in a background thread; `GET /api/onboarding/download-progress` reports byte-level progress via cache-dir polling. New "Download" wizard step with per-model progress bars + skip option. |
| **Reconfigure engines** | ✅ done. `/onboarding` is always accessible. New "engine: <name>" pill in the topbar (cobalt dot for cloud, green for local) links to the wizard. |
| **Per-segment voice / pace overrides** | ✅ done. `Segment.voices: dict[lang, str]` + `Segment.paces: dict[lang, str]`. `Project.voice_for(lang, segment)` resolves through segment → project → language-default hierarchy. `PATCH /api/projects/{pid}/segments/{sid}/override` with Pydantic-`model_fields_set` semantics. Per-cell inline `<select>`s in the editor — picking a non-default highlights cobalt and saves. |
| **All language coverage** | ✅ done. 23 languages (English + 22 Indic) where IndicTrans2 + Indic Parler-TTS overlap. Per-language voice pool from the model card. New projects default to en + hi. |
| **install.py daily-launcher polish** | ✅ done. Walks ports 8765..8775 when 8765 is busy; fast-skips pip when egg-info ≥ pyproject.toml mtime. Bootstrap page probes the same range. |
| **M2.1–2.5 — IVR + telephony** | pending (Tier 2 — see milestones below) |

## Context

prashnam-voice today is a working local-first multilingual audio generator built around a
polling workflow (1 question + N options × 10 Indic languages). We want to repackage it
as a redistributable product that handles broader use cases — generic announcements
in Tier 1, IVR menus in Tier 2 — while keeping the "no servers, runs on your laptop"
property.

## Decisions locked

1. **Default engine = local.** Reasons: free, private, fully offline. The model download
   (~4.5 GB on first run) is the cost of admission.
2. **Cloud is the side door.** If the user picks cloud, **Sarvam.ai is the default
   recommendation** for its Indic-first voice quality. Other adapters
   (ElevenLabs, Google Cloud TTS, OpenAI TTS, Bhashini) are listed but unselected.
3. **Bundle PyTorch unconditionally.** Default path needs it. Adds ~700 MB to the
   `.app` / `.exe`. Models still download lazily.
4. **Tier 1 domains: `poll` + `announcement` only.** IVR is Tier 2.
5. **Cross-platform:** macOS first, Windows next, Linux AppImage if cheap.
6. **No backend services.** Everything runs locally. Cloud adapters call third-party
   APIs directly from the user's machine; we never proxy.

## Architecture

```
                    ┌────────────────────────────────────┐
                    │ Web UI (vanilla JS)                │
                    │ + CLI (Typer)                      │
                    └──────────────┬─────────────────────┘
                                   │ REST + Python API
                    ┌──────────────▼─────────────────────┐
                    │ Pipeline / Job Queues               │
                    │ (translate queue + audio queue)     │
                    └──────────────┬─────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
 ┌────────────┐           ┌────────────────┐           ┌─────────────┐
 │ Project    │           │ Engine adapters│           │ Domain packs│
 │ store      │           │  (translator + │           │  - poll     │
 │ (JSON +    │           │   tts)         │           │  - announce │
 │ audio dir) │           │  - local       │           │  - ivr (T2) │
 └────────────┘           │  - sarvam      │           └─────────────┘
                          │  - elevenlabs  │
                          │  - google      │
                          │  - openai      │
                          │  - bhashini    │
                          └────────────────┘
```

Three new layers vs. today's code: **engine adapters**, **domain packs**, and an
**onboarding wizard** that sits on top of both.

---

## Tier 1 milestones

### M1.1 — Engine adapter layer  (~5 days)

Refactor `Translator` and `TTS` from concrete classes into protocols + registry.

**New module `prashnam_voice/adapters/`:**

```
adapters/
├── __init__.py         # registry + public API
├── base.py             # TranslatorAdapter, TTSAdapter Protocols + Setting/Voice
├── local/
│   ├── translator.py   # current IndicTrans2 wrapper, now an adapter
│   └── tts.py          # current Indic Parler-TTS wrapper, now an adapter
├── sarvam/             # Sarvam.ai REST adapter
├── elevenlabs/
├── google/
├── openai/
└── bhashini/
```

**Key types:**

```python
@dataclass
class Setting:
    key: str           # "api_key"
    label: str         # "API key"
    help: str          # "Get one at https://dashboard.sarvam.ai"
    secret: bool = True
    url: str | None = None   # link to render in onboarding

@dataclass
class Voice:
    id: str
    name: str
    lang: str
    gender: str | None = None
    sample_url: str | None = None

class TranslatorAdapter(Protocol):
    name: str
    label: str
    supports_offline: bool
    supported_langs: list[str]
    needs_setup: list[Setting]
    def is_configured(self, cfg: dict) -> bool: ...
    def translate_batch(self, texts, lang, cfg) -> list[str]: ...

class TTSAdapter(Protocol):
    name: str
    label: str
    supports_offline: bool
    supported_langs: list[str]
    needs_setup: list[Setting]
    def is_configured(self, cfg: dict) -> bool: ...
    def voices_for(self, lang, cfg) -> list[Voice]: ...
    def synthesize(self, text, lang, voice, pace, cfg) -> bytes: ...
```

**Registry:**
```python
register_adapter(adapter)
get_adapter(name)
list_translator_adapters()
list_tts_adapters()
```

**Config persistence (`~/.config/prashnam-voice/config.json`):**
```json
{
  "translator": {"name": "local-ai4bharat", "settings": {}},
  "tts": {"name": "local-ai4bharat", "settings": {}}
}
```
Settings stored per-adapter so switching back later doesn't lose API keys.

**Adapters shipped in M1.1:**
- `local-ai4bharat` (current implementation, refactored)
- `sarvam` (REST: translate + TTS endpoints)

**Exit criteria:**
- All current tests still pass
- New tests for adapter contracts (`test_adapter_contracts.py`)
- Web app works with both `local-ai4bharat` and `sarvam` selected via config
- CLI: `prashnam-voice engines list` + `prashnam-voice engines configure`

### M1.2 — Pronunciation lexicon  (~2 days)

Per-project `lexicon` dict on `Project`:
```json
{
  "lexicon": {
    "global": {"BJP": "bee jay pee", "AAP": "ay ay pee"},
    "hi": {"BJP": "बीजेपी"}
  }
}
```

Apply in `effective_text(...)` *after* template wrapping, before sending to translator/TTS.
Per-language entries override globals. Editable in the project settings panel
(simple textarea: `KEY=value` per line).

**Exit criteria:** lexicon mapping persists, regen reflects substitutions, tests cover
both global + per-lang.

### M1.3 — Domain packs  (~4 days)

A **domain pack** declares:
- Segment types (poll: `question`, `option`; announcement: `body`)
- Default templates
- Validation rules (e.g. polling needs ≥1 question + ≥1 option)
- Optional view hooks (custom segment renderer; defaults to the current list view)

```python
@dataclass
class DomainPack:
    name: str                  # "poll"
    label: str                 # "Polling"
    description: str
    segment_types: list[SegmentTypeSpec]
    default_templates: dict[str, str]   # type -> template string
    validate: Callable[[Project], list[str]]   # returns error strings, [] if OK
```

**Tier 1 domains:**
- `poll` — current behavior (question + options)
- `announcement` — single segment of type `body`, no per-option numbering

**Project field:** `domain: str = "poll"`. Set at creation, immutable.

**Exit criteria:**
- Both domains creatable from web UI + CLI
- Web UI hides "Add option" / templates that don't apply to the current domain
- Tests cover validation per domain

### M1.4 — Bulk CSV import  (~2 days)

CLI: `prashnam-voice batch <file.csv> --domain poll --langs all`.

CSV schema (poll): `group_id,type,english`. Rows with the same `group_id` form
one project. Names default to the first row's text.

CSV schema (announcement): `group_id,english`. One project per row, optionally
grouped.

Web UI: file picker that imports into a new or existing project.

**Exit criteria:** import 50 rows in <30 s (no model calls, just project creation +
segment population).

### M1.5 — Promoted Python API  (~2 days)

Curate `prashnam_voice.public` with stable surface:
- `Project`, `Segment`, `ProjectStore`
- `Pipeline.translate_segments`, `Pipeline.synthesize_segment_lang`
- `register_adapter`, `register_domain`
- `Engine.get_translator()`, `Engine.get_tts()`

Plus a single-page `docs/api.md` showing how to embed the engine in a third-party
app without going through the web UI.

**Exit criteria:** an example script that creates a project, adds a segment,
synthesizes audio in 3 langs, all in ~20 lines.

### M1.x — Onboarding wizard  (~3 days, runs in parallel with M1.1)

First-launch flow detected by absence of `~/.config/prashnam-voice/config.json`.

```
1. Welcome
   "prashnam-voice — multilingual audio generation. Let's get you set up."
   [Continue]

2. Default path: local
   "We'll run AI4Bharat models on this Mac. Free, private, ~4.5 GB one-time download."
   [Use local (recommended)] [Use a cloud service instead]

   If "local":
     2a. System check
         - Apple Silicon detected: ✓
         - 24 GB RAM detected: ✓
         - 8 GB free disk: ✓
     [Start download] → progress bars for IndicTrans2 + Indic Parler-TTS
     [Skip — I'll download later]

   If "cloud":
     2b. Pick provider
         (•) Sarvam.ai      — best Indic voices ($)
         ( ) ElevenLabs     — premium quality ($$)
         ( ) Google Cloud
         ( ) OpenAI TTS
         ( ) Bhashini       — government, free, uneven
     [Continue]
     2c. Setup form rendered from adapter.needs_setup
         "Get an API key:
            1. Open https://dashboard.sarvam.ai  [Open in browser]
            2. Sign in
            3. Create API key
            4. Paste below: [______________]"
         [Test connection] → green check or red error
         [Continue when green]

3. First project
   "All set. Pick a starter:"
   [+ New polling project]
   [+ New announcement project]
   [Skip — open empty editor]
```

Each adapter's `needs_setup` list drives Step 2c — fields render automatically.
"Test connection" calls a tiny synth-and-discard. "Open in browser" routes through
the existing `subprocess.run(["open", url])` path.

Re-runnable from Settings → "Reconfigure engines".

**Exit criteria:** fresh install → onboarding → working first project, no terminal.

**Tier 1 effort total: ~3 weeks.**

---

## Tier 2 milestones

### M2.1 — IVR domain pack  (~4 days)

New domain `ivr` with:
- Segment types: `prompt`, `menu`, `response`, `bridge`, `terminator`
- `Segment.edges: dict[str, str]` mapping DTMF input → next segment id
  (also `"timeout"` and `"invalid"` keys)
- `next_on(input)` helper

JSON migration: existing projects (poll/announcement) get `edges = {}` automatically.

**Exit criteria:** create an IVR project, add 5 segments, wire 1→2 on "1" and 1→3 on "2",
serialize to disk, reload.

### M2.2 — DAG editor view  (~6 days)

Custom view registered by the IVR domain. Two-pane layout:
- Left: graph canvas. Nodes = segments. Edges drawn from each "press N" handle.
  Click a node to open the same segment editor we already have.
  Drag from a node's output port to another node to wire.
- Right: same per-language audio matrix as today, scoped to the selected node.

Implementation: vanilla SVG rendering of nodes + bezier edges. No cytoscape dep.
Drag-to-connect via mousedown on output port → mousemove draws preview line →
mouseup on target node creates the edge. ~400 LOC of JS.

**Exit criteria:** can build a 10-node IVR tree without touching JSON.

### M2.3 — Walk simulator  (~3 days)

In the DAG view, "▶ Walk" mode:
- Highlight the currently-active segment, autoplay its audio
- DTMF panel (1–9, 0, *, #) and "timeout" / "invalid" buttons
- Pressing a button finds the matching edge, advances, repeats
- Dead ends (no edges) show "(call ends)"
- "Reset" returns to the project's designated start segment

**Exit criteria:** walk a 5-deep tree without leaving the editor.

### M2.4 — Telephony output codecs  (~3 days)

Beyond MP3, add output adapters keyed off project setting:
- `g711-ulaw` (`.wav` 8 kHz µ-law) — universal IVR
- `g711-alaw` — European telephony
- `gsm` — feature-phone IVR
- `opus` — modern WebRTC

All via existing ffmpeg dep.

Project setting: `output_codecs: list[str]` (default: `["mp3"]`). On regen, all listed
codecs produced from the same source WAV.

**Exit criteria:** generated G.711 file plays correctly through Asterisk / Twilio.

### M2.5 — Telephony platform packaging  (~3 days)

On project export, also produce:
- `twilio.xml` — TwiML referencing relative URLs
- `exotel.json`
- `plivo.xml`

Pure templating from the segment graph. No platform integration; user uploads the
ZIP themselves.

**Exit criteria:** Twilio sandbox accepts the export.

### M2.x — Distribution polish  (~1 day)

> **Revised.** PyInstaller bundle replaced with the bootstrap-from-source model.

- `index.html` at the repo root: file:// landing page that polls
  `localhost:8765/api/health` to detect when the local server is up.
- `install.py` at the repo root: cross-platform Python script that creates a
  venv, installs the package, launches the server, and opens the bootstrap page.
- `/onboarding` route in the running server: HF gating + token entry, or
  Sarvam key entry. Persists to `~/.config/prashnam-voice/config.json` and
  flips `onboarded: true`. The bootstrap page detects the flag and routes
  the user to `/` for the main app.

Why we dropped PyInstaller:
- `git clone` → double-click → working app is achievable without
  per-platform builds, code-signing, or notarization.
- 5 MB repo download instead of 700 MB bundle. User installs Python once
  themselves (we link to 3.11 specifically since 3.13/3.14 lack ML wheels).
- Updates are `git pull`, not re-download + re-sign + redistribute.

**Tier 2 effort total: ~3–4 weeks.**

---

## What's deliberately out of scope

- **Auth, multi-tenant, hosted SaaS.** (Tier 3.)
- **Real-time/streaming TTS.** Polls + IVR are pre-recorded.
- **Voice cloning UI.** Adapters can support it, but no first-class browser flow.
- **Custom acoustic model fine-tuning.** Out of band.
- **Mobile / Electron app.** Web app on localhost is the UI; the `.app` is just a
  launcher.
- **Live editing collaboration.** Single-user assumption stays.

---

## Implementation order

| Week | Milestone | Notes |
|---|---|---|
| 1 | M1.1.a-d (adapter interfaces + local refactor) | foundation |
| 2 | M1.1.e (Sarvam) + M1.x onboarding skeleton | first cloud adapter; first-launch flow |
| 3 | M1.2 lexicon + M1.3 domain packs (poll + announcement) | critical UX features |
| 4 | M1.4 CSV import + M1.5 public API + finish onboarding | wraps Tier 1 |
| 5–6 | M2.1 IVR domain + M2.2 DAG editor | biggest piece of Tier 2 |
| 7 | M2.3 walk simulator + M2.4 codecs | makes IVR practically usable |
| 8 | M2.5 platform packaging + M2.x distribution polish | shipping |

8 weeks of focused work. Compresses to 6 if M2.5 platform exports get pushed to
"as needed" and Linux AppImage drops.

---

## Risks

1. **PyInstaller + Apple Silicon native code signing** is fiddly. Allow 2 days of
   slop in the packaging week.
2. **Sarvam REST API stability** — they're a young company; expect schema drift.
   Pin against a specific API version, document it.
3. **DAG editor scope creep** — graph editing is famously open-ended (auto-layout?
   minimap? undo?). Time-box M2.2 hard at 6 days; ship rough edges.
4. **Model license re-check before public release.** Parler-TTS = Apache 2.0,
   IndicTrans2 = MIT — both commercial-friendly. Audit speaker datasets in the
   weights before bundling them in any installer.

---

## Today's starting point: M1.1

Begin with adapter interface + refactor existing code into the local adapter.
No new behavior, just structure. This is the load-bearing change for everything else.
