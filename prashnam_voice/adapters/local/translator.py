"""Local translator adapter — wraps the existing IndicTrans2 implementation.

Owns the singleton lifecycle of the underlying model: lazily loads on
first call, frees on `close()`. The framework's pipeline closes the
translator before loading TTS to keep peak RAM down.

The `cfg` dict (from app_config) may carry an `hf_token` field — that's
the read token the user pasted in onboarding. We export it as the
`HF_TOKEN` env var before huggingface_hub kicks in so model downloads
authenticate.
"""
from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from ...config import ALL_LANG_CODES, TRANSLATION_MODEL
from ..base import AdapterError, Setting

if TYPE_CHECKING:
    from ...translator import Translator as _CoreTranslator


class LocalTranslator:
    name = "local-ai4bharat"
    label = "AI4Bharat IndicTrans2 (local)"
    description = (
        "Runs Meta-style transformer translator on this computer. "
        "Free, private, offline after a one-time ~800 MB download."
    )
    supports_offline = True
    supported_langs = list(ALL_LANG_CODES)
    needs_setup = [
        Setting(
            key="hf_token",
            label="Hugging Face read token",
            help="Pasted during onboarding. Used to download the gated AI4Bharat models.",
            secret=True,
            url="https://huggingface.co/settings/tokens",
            required=False,    # may be set via huggingface-cli login env instead
        ),
    ]

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._impl: "_CoreTranslator | None" = None

    def is_configured(self, cfg: dict) -> bool:
        return True

    def _ensure(self, cfg: dict | None = None) -> "_CoreTranslator":
        with self._lock:
            if self._impl is None:
                _export_hf_token(cfg)
                # Lazy import keeps `import prashnam_voice` cheap when only
                # cloud adapters end up being used.
                from ...translator import Translator
                self._impl = Translator(model_id=TRANSLATION_MODEL)
            return self._impl

    def translate_batch(
        self, texts: list[str], lang: str, cfg: dict
    ) -> list[str]:
        if lang not in self.supported_langs:
            raise AdapterError(self.name, f"unsupported language: {lang}")
        if not texts:
            return []
        try:
            return self._ensure(cfg).translate_batch(texts, lang)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(self.name, f"translation failed: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._impl is not None:
                self._impl.close()
                self._impl = None


def _export_hf_token(cfg: dict | None) -> None:
    """Populate HF_TOKEN / HUGGING_FACE_HUB_TOKEN from cfg if the user hasn't
    set them already at the shell level. setdefault preserves any existing
    value (e.g. from `huggingface-cli login`).
    """
    token = (cfg or {}).get("hf_token") if cfg else None
    if not token:
        return
    os.environ.setdefault("HF_TOKEN", token)
    os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)
