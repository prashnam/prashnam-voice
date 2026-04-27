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


# Per-language speaker pool from the Indic Parler-TTS model card.
# Languages without an entry fall back to `_PARLER_FALLBACK_POOL` below.
_PARLER_SPEAKERS_BY_LANG: dict[str, tuple[str, ...]] = {
    "en":  ("Mary", "Thoma", "Swapna", "Dinesh", "Meera", "Jatin", "Aakash",
            "Sneha", "Kabir", "Tisha", "Chingkhei", "Thoiba", "Priya",
            "Tarun", "Gauri", "Nisha", "Raghav", "Kavya", "Ravi", "Vikas", "Riya"),
    "hi":  ("Rohit", "Divya", "Aman", "Rani"),
    "ta":  ("Kavitha", "Jaya"),
    "te":  ("Prakash", "Lalitha", "Kiran"),
    "bn":  ("Arjun", "Aditi", "Tapan", "Rashmi", "Arnav", "Riya"),
    "mr":  ("Sanjay", "Sunita", "Nikhil", "Radha", "Varun", "Isha"),
    "kn":  ("Suresh", "Anu", "Chetan", "Vidya"),
    "gu":  ("Yash", "Neha"),
    "pa":  ("Divjot", "Gurpreet"),       # unofficial in Parler
    "ml":  ("Anjali", "Anju", "Harish"),
    "or":  ("Manas", "Debjani"),
    "as":  ("Amit", "Sita", "Poonam", "Rakesh"),
    "ne":  ("Amrita",),
    "sa":  ("Aryan",),
    "brx": ("Bikram", "Maya", "Kalpana"),
    "doi": ("Karan",),
    "mni": ("Laishram", "Ranjit"),
    # Languages without a card-listed speaker (Konkani, Maithili, Kashmiri,
    # Sindhi, Santali, Urdu) fall through to the generic pool.
}
# Speakers that perform reasonably across most Indic langs when the model
# card doesn't publish a specific list.
_PARLER_FALLBACK_POOL: tuple[str, ...] = (
    "Aman", "Divya", "Rohit", "Aditi", "Sanjay", "Anjali", "Suresh",
    "Arjun", "Manas", "Aryan",
)


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
        # Per-language speaker pool sourced from the Indic Parler-TTS model
        # card. Parler accepts any speaker string at the API level — the
        # description prompt does the heavy lifting — so consumers are free
        # to pass anything. We surface the model-card-recommended pool plus
        # the project's chosen default voice (typically already in the pool).
        spec = LANGUAGES.get(lang)
        if not spec:
            return []
        pool = list(_PARLER_SPEAKERS_BY_LANG.get(lang, ()))
        if not pool:
            # Languages with no listed speakers — fall back to a small
            # general-purpose set that works decently across Indic langs.
            pool = list(_PARLER_FALLBACK_POOL)
        seen: set[str] = set()
        out: list[Voice] = []
        # Always surface the project's configured default first.
        for vid in [spec.voice, *pool]:
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
