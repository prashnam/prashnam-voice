from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from .config import AUDIO_CACHE_DIR, TTS_MODEL


def _key(
    text: str,
    lang_code: str,
    voice: str,
    pace: str,
    model_id: str = TTS_MODEL,
) -> str:
    payload = f"{model_id}|{lang_code}|{voice}|{pace}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def cache_path(
    text: str,
    lang_code: str,
    voice: str,
    pace: str,
    model_id: str = TTS_MODEL,
) -> Path:
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIO_CACHE_DIR / f"{_key(text, lang_code, voice, pace, model_id)}.mp3"


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
