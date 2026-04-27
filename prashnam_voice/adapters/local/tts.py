"""Local TTS adapter — wraps the existing Indic Parler-TTS implementation.

Lazy load + close lifecycle. Synthesizes WAV in memory then encodes to MP3
via ffmpeg/pydub so the framework gets bytes back.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from ...config import ALL_LANG_CODES, LANGUAGES, TTS_MODEL
from ..base import AdapterError, Setting, Voice

if TYPE_CHECKING:
    from ...tts import TTS as _CoreTTS

log = logging.getLogger(__name__)


class LocalTTS:
    name = "local-ai4bharat"
    label = "Indic Parler-TTS (local)"
    description = (
        "Runs the Parler-TTS multilingual model on this computer. "
        "Free, private, offline after a one-time ~3.6 GB download."
    )
    supports_offline = True
    supported_langs = list(ALL_LANG_CODES)
    needs_setup = [
        Setting(
            key="hf_token",
            label="Hugging Face read token",
            help="Same token as the translator. Used to download Indic Parler-TTS.",
            secret=True,
            url="https://huggingface.co/settings/tokens",
            required=False,
        ),
    ]

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._impl: "_CoreTTS | None" = None

    def is_configured(self, cfg: dict) -> bool:
        return True

    def _ensure(self, cfg: dict | None = None) -> "_CoreTTS":
        with self._lock:
            if self._impl is None:
                token = (cfg or {}).get("hf_token") if cfg else None
                if token:
                    os.environ.setdefault("HF_TOKEN", token)
                    os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)
                from ...tts import TTS
                self._impl = TTS(model_id=TTS_MODEL)
            return self._impl

    def voices_for(self, lang: str, cfg: dict) -> list[Voice]:
        # Parler exposes the same speaker pool across all langs; we ship
        # the curated default per language plus a few alternates the model
        # card recommends. Adapter consumers are free to pass any speaker
        # string — Parler will accept it and the description prompt does
        # the heavy lifting.
        spec = LANGUAGES.get(lang)
        if not spec:
            return []
        defaults = ["Divya", "Aditi", "Anjali", "Manasi", "Aryan",
                    "Rohit", "Sanjay", "Suresh", "Arjun", "Yash"]
        seen = set()
        out: list[Voice] = []
        for vid in [spec.voice, *defaults]:
            if vid in seen:
                continue
            seen.add(vid)
            out.append(Voice(id=vid, name=vid, lang=lang))
        return out

    def synthesize(
        self,
        text: str,
        lang: str,
        voice: str,
        pace: str,
        cfg: dict,
    ) -> bytes:
        if lang not in self.supported_langs:
            raise AdapterError(self.name, f"unsupported language: {lang}")
        try:
            impl = self._ensure(cfg)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(self.name, f"failed to load model: {exc}") from exc

        # Synthesize to a temp WAV, then transcode to MP3 in memory.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            impl.synthesize_to_wav(text, lang, wav_path, voice=voice, pace=pace)
            return _wav_to_mp3_bytes(wav_path)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(self.name, f"synthesis failed: {exc}") from exc
        finally:
            wav_path.unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            if self._impl is not None:
                self._impl.close()
                self._impl = None


def _wav_to_mp3_bytes(wav_path: Path) -> bytes:
    """Transcode a WAV file to MP3 bytes using pydub. Mono, 96 kbps —
    matches the project's existing audio.MP3_BITRATE.
    """
    from pydub import AudioSegment

    buf = io.BytesIO()
    AudioSegment.from_wav(wav_path).set_channels(1).export(
        buf, format="mp3", bitrate="96k"
    )
    return buf.getvalue()
