# Embedding prashnam-voice in Python code

> The HTTP / REST counterpart of this doc lives at
> [**rest-api.md**](rest-api.md). Use this one if you're importing
> `prashnam_voice` directly; use the REST one if you're driving the
> running server over HTTP from a different language or runtime.

Everything in this document lives under `prashnam_voice.public`. That module
is the stable contract — internal modules can shift between releases, but
public re-exports won't (deprecation warnings if anything ever does).

```python
from prashnam_voice.public import (
    ProjectStore, Pipeline, Engine,
    register_translator, register_tts, register_domain,
)
```

## 1. Create a project, add segments, generate audio

```python
from pathlib import Path
from prashnam_voice.public import ProjectStore, Pipeline

store = ProjectStore(Path("./projects"))
proj = store.create("Election 2026", langs=["en", "hi", "ta"], domain="poll")

store.add_segment(proj.id, "question", "Who will win the upcoming election?")
store.add_segment(proj.id, "option", "Party A")
store.add_segment(proj.id, "option", "Party B")
store.add_segment(proj.id, "option", "Party C")

# Re-load to pick up the new segments.
proj = store.load(proj.id)

# Translate + synthesize for one segment in all languages.
seg = next(s for s in proj.segments if s.type == "question")
Pipeline.regenerate_segment(store, proj.id, seg.id, ["en", "hi", "ta"])
```

After this returns:
- Translations are stored on `proj.segments[*].translations[lang]`.
- Audio MP3s land at `projects/<proj.id>/audio/<seg.id>/<lang>/<attempt_id>.mp3`.
- `seg.current_takes[lang]` points at the attempt id you just produced.

## 2. Granular operations

If you don't want the full regenerate flow, use the parts directly.

```python
# Just translate; no audio.
Pipeline.translate_segments(proj, proj.segments, ["en", "hi"])
store.update(proj)

# Just synthesize one (segment, lang) and don't set it as the current take.
attempt_id = Pipeline.synthesize_segment_lang(store, proj, seg, "hi")
store.set_current_take(proj.id, seg.id, "hi", attempt_id)
```

Both call into the configured engine via `Engine.get_translator()` /
`Engine.get_tts()` under the hood.

## 3. Swap engines (e.g. local → Sarvam)

The active adapter is read from `AppConfig`. To switch engines:

```python
from prashnam_voice.public import (
    update_config, AppConfig, Engine,
)

def _switch(cfg: AppConfig) -> None:
    cfg.translator.name = "sarvam"
    cfg.tts.name = "sarvam"
    cfg.translator.all_settings["sarvam"] = {"api_key": "sk_..."}
    cfg.tts.all_settings["sarvam"] = {"api_key": "sk_..."}

update_config(_switch)
Engine.release()    # drop the cached adapter so the new one loads next call
```

`AppConfig` keeps every adapter's settings side by side, so flipping back to
`local-ai4bharat` later doesn't lose your Sarvam key.

## 4. Register a custom adapter

Implement the `TranslatorAdapter` / `TTSAdapter` protocols and register at
import time.

```python
from prashnam_voice.public import (
    Setting, Voice, AdapterError,
    register_translator, register_tts,
)


class GoogleCloudTTS:
    name = "google-cloud-tts"
    label = "Google Cloud TTS"
    description = "Google's neural voices over their API."
    supports_offline = False
    supported_langs = ["en", "hi", "ta", "te", "bn", "mr", "kn", "gu", "ml"]
    needs_setup = [
        Setting(
            key="service_account_json",
            label="Service-account JSON",
            help="Paste the contents of your service-account JSON.",
            secret=True,
            url="https://console.cloud.google.com/apis/credentials",
        ),
    ]

    def is_configured(self, cfg: dict) -> bool:
        return bool(cfg.get("service_account_json"))

    def voices_for(self, lang: str, cfg: dict) -> list[Voice]:
        # Hit Google's voices.list endpoint, filter by lang, return Voice(...).
        ...

    def synthesize(self, text, lang, voice, pace, cfg) -> bytes:
        if not self.is_configured(cfg):
            raise AdapterError(self.name, "missing JSON", setup_required=True)
        # Call texttospeech.googleapis.com, return MP3 bytes.
        ...

    def close(self) -> None:
        pass


register_tts(GoogleCloudTTS())
```

After import, the adapter shows up in `list_tts()` and the onboarding wizard
will render its setup form automatically.

## 5. Register a custom domain

A `DomainPack` declares the segment types and validation rules for a project
shape. `poll`, `announcement`, and `ivr` come built in; here's an
`ivr-leg` example for a smaller, single-menu shape.

```python
from prashnam_voice.public import (
    DomainPack, SegmentTypeSpec, register_domain,
)


def _validate_ivr_leg(project) -> list[str]:
    if not any(s.type == "prompt" for s in project.segments):
        return ["IVR leg needs at least one prompt segment"]
    return []


IVR_LEG = DomainPack(
    name="ivr-leg",
    label="IVR Leg",
    description="Single IVR menu node (prompt + 1..N response handles).",
    segment_types=[
        SegmentTypeSpec("prompt",   "Prompt",   addable=False, deletable=False, max=1),
        SegmentTypeSpec("response", "Response", addable=True,  deletable=True),
    ],
    default_templates={
        "question_template": "{body}",
        "option_template":   "Press {n} for {body}.",
    },
    validate=_validate_ivr_leg,
)
register_domain(IVR_LEG)
```

Once registered, projects can be created with `domain="ivr-leg"`; the web
editor's "+ Add ..." button and segment-type validation pick up your spec
automatically.

## 5b. Building an IVR call flow

The built-in `ivr` domain ships with five segment types
(`prompt`, `menu`, `response`, `bridge`, `terminator`) plus a DAG of
DTMF-keyed edges between them. The DAG editor in the web app is built
on top of the same `ProjectStore` calls shown below.

```python
from prashnam_voice.public import ProjectStore
from prashnam_voice.projects import DTMF_KEYS, SPECIAL_EDGE_KEYS  # ("1"…"#") + ("timeout","invalid")

store = ProjectStore(Path("./projects"))
proj  = store.create("Customer support flow", langs=["en", "hi"], domain="ivr")

greeting = store.add_segment(proj.id, "prompt", "Welcome to Prashnam.")
menu     = store.add_segment(proj.id, "menu",
                             "Press 1 for support, 2 for sales, 0 to repeat.")
support  = store.add_segment(proj.id, "response", "Connecting you to support.")
sales    = store.add_segment(proj.id, "response", "Connecting you to sales.")

# Wire the menu's DTMF edges.
store.set_segment_edge(proj.id, menu.id, "1", support.id)
store.set_segment_edge(proj.id, menu.id, "2", sales.id)
store.set_segment_edge(proj.id, menu.id, "0", greeting.id)        # repeat the greeting
store.set_segment_edge(proj.id, menu.id, "timeout", greeting.id)  # caller said nothing
store.set_segment_edge(proj.id, menu.id, "invalid", greeting.id)  # caller pressed something unmapped

# Edge validation: keys must be in DTMF_KEYS or SPECIAL_EDGE_KEYS, target
# must exist in the same project, and self-loops raise ValueError.
# To clear an edge, pass target=None.

# Persist node positions for the DAG canvas.
store.set_segment_position(proj.id, greeting.id, 80,  80)
store.set_segment_position(proj.id, menu.id,     80,  220)
store.set_segment_position(proj.id, support.id,  340, 160)
store.set_segment_position(proj.id, sales.id,    340, 280)

# Pin the entry point. Empty string ("") means "first segment by declaration".
store.set_start_segment(proj.id, greeting.id)

# Validate the graph (dangling edges, empty menus, unknown start, …).
from prashnam_voice import domains as domains_mod
errs = domains_mod.get("ivr").validate(store.load(proj.id))
assert not errs, errs

# Walk the graph in code (the web app's "walk simulator" does the same):
proj  = store.load(proj.id)
node  = proj.resolve_start_segment()
while node and node.type != "terminator":
    print(node.id, node.type, node.english)
    next_id = node.edges.get("1") or node.edges.get("timeout")
    node = proj.find_segment(next_id) if next_id else None
```

Cleanup is automatic: `store.delete_segment(proj.id, sales.id)` removes
both `sales` and any inbound edges (here: `menu.edges["2"]`), and clears
`start_segment_id` if it pointed at the deleted node.

## 6. Bulk CSV import

```python
from prashnam_voice.public import import_csv, ProjectStore

store = ProjectStore(Path("./projects"))
result = import_csv(
    "polls.csv",
    store,
    domain="poll",
    langs=["en", "hi", "ta"],
)

print(f"created {len(result.projects)} projects")
for err in result.errors:
    print(f"  line {err.line_no}: {err.message}")
```

CSV schemas are documented in [`README.md`](../README.md) and at the
import dialog in the web UI. Errors are non-fatal — bad rows are reported,
good rows still import.

## 7. Per-segment voice / pace overrides

By default the voice and pace used for synthesis come from the
project's per-language settings. You can override per-segment, per-language:

```python
store.set_segment_overrides(
    proj.id, segment.id,
    voice=("hi", "Aman"),    # use Aman for this option's Hindi take
    pace=("ta", "slow"),     # render Tamil at half speed
)

# Clear an override:
store.set_segment_overrides(proj.id, segment.id, voice=("hi", None))
```

At synthesis time, the resolution order is:

> `segment.voices[lang]` → `project.voices[lang]` → `LANGUAGES[lang].voice`

Same for pace. Setting an override invalidates that language's cached
audio takes (the synthesis differs); translations survive.

The full per-language voice pool from the active TTS adapter:

```python
from prashnam_voice.public import Engine

tts, cfg = Engine.get_tts()
for v in tts.voices_for("hi", cfg):
    print(v.id, v.name)
```

## 8. Model download progress

When using the local engine, the AI4Bharat models (~4.5 GB) download on
first use. The `prashnam_voice.onboarding` module exposes a tracker:

```python
from prashnam_voice import onboarding

onboarding.start_model_download(token="hf_...")    # non-blocking
job = onboarding.get_download_progress()           # poll
print(job.state)                  # "idle" | "running" | "done" | "error"
for mid, mp in job.models.items():
    print(mid, mp.downloaded_bytes, "/", mp.total_bytes)
```

Single-flight: calling `start_model_download` while a download is
running is a no-op. The progress is computed by polling the Hugging
Face cache directory size, so it works regardless of how huggingface_hub
chooses to fetch (multi-file, parallel, resumed, etc.).

## 9. Numerals & lexicon

Both run in `effective_text(project, segment, lang)` which is what the
pipeline actually feeds the translator/TTS. You probably never need to
call them directly, but if you want the same normalization on text that
isn't in a project:

```python
from prashnam_voice.public import numerals_to_words

numerals_to_words("press 1")          # → "press one"
numerals_to_words("year 2026")        # → "year two thousand and twenty-six"
numerals_to_words("BJP123 alpha")     # → "BJP123 alpha"  (alphanumerics intact)
```

The lexicon is per-project — set via `ProjectStore.update_settings(...,
lexicon={...})` or by editing `project.json` directly.

## 10. Configuration paths

| File | Purpose |
|---|---|
| `~/.config/prashnam-voice/config.json` | Adapter selection + per-adapter settings (API keys). Single user, single host. |
| `<projects_root>/<project_id>/project.json` | Per-project state: name, segments, langs, paces, voices, lexicon, templates. |
| `<projects_root>/<project_id>/audio/<seg>/<lang>/<att>.mp3` | One MP3 per regen attempt. `current_takes` in `project.json` points at one. |
| `~/.cache/prashnam-voice/audio/<sha>.mp3` | Content-addressed audio cache. Survives across projects. |

Override the config path with `set_config_path(path)` (useful for tests
and for shipping multiple installations side-by-side).
