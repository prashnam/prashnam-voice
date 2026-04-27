"""Process-wide singletons for the active translator + TTS adapters.

Adapters are looked up by name from the registry. The first call to
get_translator/get_tts triggers `ensure_builtins_loaded`, which imports
the built-in adapter modules (local, sarvam, ...). Each module
self-registers on import.

Switching adapters at runtime (e.g. user changes engine in settings) is
handled by `release()` which closes any loaded model and clears the
cache; the next call rebuilds with the new config.
"""
from __future__ import annotations

import threading
import time

from . import adapters as adapter_registry
from . import app_config
from .adapters.base import TranslatorAdapter, TTSAdapter

_LOCK = threading.Lock()
_translator: TranslatorAdapter | None = None
_translator_cfg: dict | None = None
_tts: TTSAdapter | None = None
_tts_cfg: dict | None = None
_last_use: float = 0.0


def get_translator() -> tuple[TranslatorAdapter, dict]:
    """Returns the active translator + its settings dict.

    The settings dict is what callers pass through to `translate_batch(...)`.
    Cached per-adapter; if the user changes adapters, call `release()` first.
    """
    global _translator, _translator_cfg, _last_use
    cfg = app_config.load()
    name = cfg.translator.name
    settings = cfg.translator.settings_for(name)
    with _LOCK:
        if _translator is None or _translator.name != name:
            if _translator is not None:
                try: _translator.close()
                except Exception: pass  # noqa: BLE001
            _translator = adapter_registry.get_translator(name)
        _translator_cfg = settings
        _last_use = time.time()
        return _translator, settings


def get_tts() -> tuple[TTSAdapter, dict]:
    global _tts, _tts_cfg, _last_use
    cfg = app_config.load()
    name = cfg.tts.name
    settings = cfg.tts.settings_for(name)
    with _LOCK:
        if _tts is None or _tts.name != name:
            if _tts is not None:
                try: _tts.close()
                except Exception: pass  # noqa: BLE001
            _tts = adapter_registry.get_tts(name)
        _tts_cfg = settings
        _last_use = time.time()
        return _tts, settings


def release() -> None:
    """Close cached adapters. Call after the user changes engine settings."""
    global _translator, _tts, _translator_cfg, _tts_cfg
    with _LOCK:
        if _translator is not None:
            try: _translator.close()
            except Exception: pass  # noqa: BLE001
            _translator = None
            _translator_cfg = None
        if _tts is not None:
            try: _tts.close()
            except Exception: pass  # noqa: BLE001
            _tts = None
            _tts_cfg = None


def last_used_at() -> float:
    return _last_use
