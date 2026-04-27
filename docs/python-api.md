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
shape. Ship `poll` + `announcement` come built in; here's an `ivr-leg`
example.

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

## 7. Numerals & lexicon

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

## 8. Configuration paths

| File | Purpose |
|---|---|
| `~/.config/prashnam-voice/config.json` | Adapter selection + per-adapter settings (API keys). Single user, single host. |
| `<projects_root>/<project_id>/project.json` | Per-project state: name, segments, langs, paces, voices, lexicon, templates. |
| `<projects_root>/<project_id>/audio/<seg>/<lang>/<att>.mp3` | One MP3 per regen attempt. `current_takes` in `project.json` points at one. |
| `~/.cache/prashnam-voice/audio/<sha>.mp3` | Content-addressed audio cache. Survives across projects. |

Override the config path with `set_config_path(path)` (useful for tests
and for shipping multiple installations side-by-side).
