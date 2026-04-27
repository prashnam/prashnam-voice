"""App-level config (which adapters are active + per-adapter settings).

Persisted at $XDG_CONFIG_HOME/prashnam-voice/config.json (or
~/.config/prashnam-voice/config.json). Distinct from per-project state.

Schema is open: each adapter owns the shape of its `settings` dict. We
just persist whatever we're handed. API keys live here in plaintext —
single-user local-only app, the same trust model as a `.env` file.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Default adapter selection. Local-first, per the locked decisions in PLAN.md.
DEFAULT_TRANSLATOR = "local-ai4bharat"
DEFAULT_TTS = "local-ai4bharat"


def config_dir() -> Path:
    """`$XDG_CONFIG_HOME/prashnam-voice` or `~/.config/prashnam-voice`."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "prashnam-voice"
    return Path.home() / ".config" / "prashnam-voice"


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class AdapterChoice:
    name: str
    # All adapters' settings are kept side-by-side so switching back doesn't
    # lose API keys. Top-level keys are adapter names; values are dicts.
    all_settings: dict[str, dict] = field(default_factory=dict)

    def settings_for(self, adapter_name: str | None = None) -> dict:
        return dict(self.all_settings.get(adapter_name or self.name, {}))


@dataclass
class AppConfig:
    translator: AdapterChoice = field(
        default_factory=lambda: AdapterChoice(DEFAULT_TRANSLATOR)
    )
    tts: AdapterChoice = field(
        default_factory=lambda: AdapterChoice(DEFAULT_TTS)
    )
    onboarded: bool = False           # set true once the wizard finishes

    # ----- I/O -----

    def to_json(self) -> dict:
        return {
            "translator": {
                "name": self.translator.name,
                "all_settings": self.translator.all_settings,
            },
            "tts": {
                "name": self.tts.name,
                "all_settings": self.tts.all_settings,
            },
            "onboarded": self.onboarded,
        }

    @classmethod
    def from_json(cls, d: dict) -> "AppConfig":
        return cls(
            translator=AdapterChoice(
                name=(d.get("translator") or {}).get("name", DEFAULT_TRANSLATOR),
                all_settings=dict((d.get("translator") or {}).get("all_settings", {})),
            ),
            tts=AdapterChoice(
                name=(d.get("tts") or {}).get("name", DEFAULT_TTS),
                all_settings=dict((d.get("tts") or {}).get("all_settings", {})),
            ),
            onboarded=bool(d.get("onboarded", False)),
        )


# ---------------------------------------------------------------------------
# Process-wide load/save with a lock so reads + writes don't race.
# ---------------------------------------------------------------------------


_LOCK = threading.Lock()
_cached: AppConfig | None = None
_path_override: Path | None = None


def set_config_path(p: Path | None) -> None:
    """Tests use this to redirect config to a tmp dir."""
    global _path_override, _cached
    with _LOCK:
        _path_override = p
        _cached = None


def _resolved_path() -> Path:
    return _path_override or config_path()


def load() -> AppConfig:
    global _cached
    with _LOCK:
        if _cached is not None:
            return _cached
        path = _resolved_path()
        if path.exists():
            try:
                _cached = AppConfig.from_json(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                log.warning("config at %s is malformed (%s); using defaults", path, exc)
                _cached = AppConfig()
        else:
            _cached = AppConfig()
        return _cached


def save(cfg: AppConfig) -> None:
    global _cached
    with _LOCK:
        path = _resolved_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg.to_json(), indent=2), encoding="utf-8")
        tmp.replace(path)
        _cached = cfg


def update(fn) -> AppConfig:
    """Atomic load → mutate → save."""
    with _LOCK:
        path = _resolved_path()
        if path.exists():
            cfg = AppConfig.from_json(json.loads(path.read_text(encoding="utf-8")))
        else:
            cfg = AppConfig()
        fn(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg.to_json(), indent=2), encoding="utf-8")
        tmp.replace(path)
        global _cached
        _cached = cfg
        return cfg
