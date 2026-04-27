"""Public Python API surface.

Everything imported here is part of prashnam-voice's stable contract for
embedding the library in third-party Python code. Internal modules
(`prashnam_voice.translator`, `prashnam_voice.tts`, `prashnam_voice.server.app`,
etc.) can change shape between releases; the names re-exported here will
not, except via deprecation warnings.

Quick start
-----------

    from pathlib import Path
    from prashnam_voice.public import (
        ProjectStore, Pipeline, AppConfig, set_config_path,
    )

    set_config_path(Path("./prashnam-config.json"))     # optional
    store = ProjectStore(Path("./projects"))

    proj = store.create("My poll", langs=["en", "hi"], domain="poll")
    store.add_segment(proj.id, "question", "Who will win?")
    store.add_segment(proj.id, "option", "Party A")
    store.add_segment(proj.id, "option", "Party B")

    proj = store.load(proj.id)
    seg = next(s for s in proj.segments if s.type == "question")
    Pipeline.regenerate_segment(store, proj.id, seg.id, ["en", "hi"])

See `docs/api.md` for adapter swapping + domain registration examples.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Project state
# ---------------------------------------------------------------------------

from .projects import (  # noqa: F401
    Project,
    Segment,
    ProjectStore,
    SEGMENT_TYPES,
    DEFAULT_DOMAIN,
    effective_text,
)

# ---------------------------------------------------------------------------
# Pipeline — the orchestrator. Exposed as a namespace class so callers get
# autocomplete on every operation without importing 5 names.
# ---------------------------------------------------------------------------


class Pipeline:
    """Stable entry points to translation + synthesis."""

    from .pipeline import (  # noqa: F401
        translate_segments,
        synthesize_segment_lang,
        regenerate_segment,
        run_pipeline,
        JobProgress,
        LangProgress,
    )


# ---------------------------------------------------------------------------
# Engine adapters
# ---------------------------------------------------------------------------

from .adapters import (  # noqa: F401
    TranslatorAdapter,
    TTSAdapter,
    Setting,
    Voice,
    AdapterError,
    register_translator,
    register_tts,
    get_translator as get_translator_adapter,
    get_tts as get_tts_adapter,
    list_translators,
    list_tts,
)


class Engine:
    """Resolve the active adapter pair for the current `AppConfig`.

    Use these when you want the *configured* engine; use
    `get_translator_adapter(name)` / `get_tts_adapter(name)` directly when
    you want a specific adapter regardless of config.
    """

    from .engines import get_translator, get_tts, release  # noqa: F401


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

from .domains import (  # noqa: F401
    DomainPack,
    SegmentTypeSpec,
    register as register_domain,
    get as get_domain,
    all_domains,
)

# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------

from .app_config import (  # noqa: F401
    AppConfig,
    AdapterChoice,
    DEFAULT_TRANSLATOR,
    DEFAULT_TTS,
    config_path,
    config_dir,
    load as load_config,
    save as save_config,
    update as update_config,
    set_config_path,
)

# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------

from .config import (  # noqa: F401
    LANGUAGES,
    LangSpec,
    ALL_LANG_CODES,
    PACE_PHRASES,
    DEFAULT_PACE,
    parse_langs,
    pace_phrase,
)
from .text_normalize import numerals_to_words  # noqa: F401
from .csv_import import (  # noqa: F401
    import_csv,
    ImportResult,
    ImportError as CsvImportError,
)


__all__ = [
    # Projects
    "Project", "Segment", "ProjectStore", "SEGMENT_TYPES",
    "DEFAULT_DOMAIN", "effective_text",
    # Pipeline
    "Pipeline",
    # Adapters
    "TranslatorAdapter", "TTSAdapter", "Setting", "Voice", "AdapterError",
    "register_translator", "register_tts",
    "get_translator_adapter", "get_tts_adapter",
    "list_translators", "list_tts",
    "Engine",
    # Domains
    "DomainPack", "SegmentTypeSpec",
    "register_domain", "get_domain", "all_domains",
    # Config
    "AppConfig", "AdapterChoice",
    "DEFAULT_TRANSLATOR", "DEFAULT_TTS",
    "config_path", "config_dir",
    "load_config", "save_config", "update_config", "set_config_path",
    # Static helpers
    "LANGUAGES", "LangSpec", "ALL_LANG_CODES",
    "PACE_PHRASES", "DEFAULT_PACE",
    "parse_langs", "pace_phrase",
    "numerals_to_words",
    "import_csv", "ImportResult", "CsvImportError",
]
