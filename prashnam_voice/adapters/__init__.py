"""Adapter registry — each translator / TTS implementation registers itself
here. The rest of the system looks up adapters by name (`get_translator`
or `get_tts`) and never imports adapter modules directly.
"""
from __future__ import annotations

import importlib
import logging
import threading
from typing import Iterable

from .base import (
    AdapterError,
    Setting,
    TranslatorAdapter,
    TTSAdapter,
    Voice,
)

log = logging.getLogger(__name__)

__all__ = [
    "AdapterError",
    "Setting",
    "TranslatorAdapter",
    "TTSAdapter",
    "Voice",
    "register_translator",
    "register_tts",
    "get_translator",
    "get_tts",
    "list_translators",
    "list_tts",
    "ensure_builtins_loaded",
]


_LOCK = threading.Lock()
_translators: dict[str, TranslatorAdapter] = {}
_ttss: dict[str, TTSAdapter] = {}
_builtins_loaded = False

# Built-in adapters — module paths to import lazily on first lookup. Order
# matters: the first translator/TTS pair becomes the default if config is
# missing.
_BUILTIN_MODULES = (
    "prashnam_voice.adapters.local",
    "prashnam_voice.adapters.sarvam",
)


def register_translator(adapter: TranslatorAdapter) -> None:
    with _LOCK:
        if adapter.name in _translators:
            log.debug("translator %s already registered; replacing", adapter.name)
        _translators[adapter.name] = adapter


def register_tts(adapter: TTSAdapter) -> None:
    with _LOCK:
        if adapter.name in _ttss:
            log.debug("tts %s already registered; replacing", adapter.name)
        _ttss[adapter.name] = adapter


def get_translator(name: str) -> TranslatorAdapter:
    ensure_builtins_loaded()
    with _LOCK:
        a = _translators.get(name)
    if a is None:
        raise KeyError(f"unknown translator adapter: {name}")
    return a


def get_tts(name: str) -> TTSAdapter:
    ensure_builtins_loaded()
    with _LOCK:
        a = _ttss.get(name)
    if a is None:
        raise KeyError(f"unknown tts adapter: {name}")
    return a


def list_translators() -> list[TranslatorAdapter]:
    ensure_builtins_loaded()
    with _LOCK:
        return list(_translators.values())


def list_tts() -> list[TTSAdapter]:
    ensure_builtins_loaded()
    with _LOCK:
        return list(_ttss.values())


def ensure_builtins_loaded() -> None:
    """Import the built-in adapter modules so they get a chance to register.

    Called lazily on first lookup so test code can pre-register fakes
    before the real adapters are loaded.
    """
    global _builtins_loaded
    with _LOCK:
        if _builtins_loaded:
            return
        _builtins_loaded = True
    for mod_path in _BUILTIN_MODULES:
        try:
            importlib.import_module(mod_path)
        except Exception as exc:  # noqa: BLE001 — adapters are optional
            log.warning("could not load adapter %s: %s", mod_path, exc)


def reset_for_tests() -> None:
    """Clear all registrations and force a re-import next time. Tests only."""
    global _builtins_loaded
    with _LOCK:
        _translators.clear()
        _ttss.clear()
        _builtins_loaded = False
