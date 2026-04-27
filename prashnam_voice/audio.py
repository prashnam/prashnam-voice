from __future__ import annotations

from pathlib import Path

from pydub import AudioSegment


MP3_BITRATE = "96k"


def wav_to_mp3(wav_path: Path, mp3_path: Path) -> Path:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.from_wav(wav_path).set_channels(1).export(
        mp3_path, format="mp3", bitrate=MP3_BITRATE
    )
    return mp3_path
