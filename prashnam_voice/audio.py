from __future__ import annotations

import io
import logging
import math
from pathlib import Path

import numpy as np
from pydub import AudioSegment

log = logging.getLogger(__name__)


MP3_BITRATE = "96k"

# ITU-R BS.1770 target for spoken-word content. Bump LOUDNESS_VERSION when the
# normalization recipe changes so the synthesis cache (keyed on model_id) skips
# pre-fix entries instead of returning quieter audio after a regenerate.
TARGET_LUFS = -16.0
LOUDNESS_VERSION = "norm-v1"

# pyloudnorm's integrated measurement needs ~3 s for a stable reading. Clips
# shorter than this are passed through unchanged — they're too short to gate.
_MIN_NORM_SAMPLES = 8000

# Per-language gain offset hard cap (dB). At ±6 dB the user can rescue a
# quiet voice without risking clipping (we still trim if the peak would
# exceed -0.3 dBFS, but a tighter range keeps the slider's "0" centered).
GAIN_RANGE_DB = 6.0


def wav_to_mp3(wav_path: Path, mp3_path: Path) -> Path:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.from_wav(wav_path).set_channels(1).export(
        mp3_path, format="mp3", bitrate=MP3_BITRATE
    )
    return mp3_path


# ---------------------------------------------------------------------------
# Loudness normalization
# ---------------------------------------------------------------------------


def normalize_loudness(
    mp3_bytes: bytes, target_lufs: float = TARGET_LUFS,
) -> bytes:
    """Return `mp3_bytes` re-encoded so that its integrated loudness matches
    `target_lufs`. Skips clips too short to measure or already silent."""
    try:
        import pyloudnorm as pyln
    except ImportError:
        log.warning("pyloudnorm not installed — skipping loudness normalization")
        return mp3_bytes

    seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    if int(seg.frame_count()) < _MIN_NORM_SAMPLES:
        return mp3_bytes

    samples = np.array(seg.get_array_of_samples()).astype(np.float32)
    scale = float(1 << (8 * seg.sample_width - 1))
    samples = samples / scale
    if seg.channels == 2:
        samples = samples.reshape(-1, 2)

    try:
        meter = pyln.Meter(seg.frame_rate)
        loudness = meter.integrated_loudness(samples)
    except Exception:  # noqa: BLE001 — pyloudnorm raises on degenerate inputs
        return mp3_bytes
    if not math.isfinite(loudness) or loudness < -70.0:
        return mp3_bytes

    gain_db = target_lufs - loudness
    gain_db = max(-12.0, min(12.0, gain_db))

    peak = float(np.max(np.abs(samples))) or 1e-9
    peak_db_after = 20.0 * math.log10(peak) + gain_db
    if peak_db_after > -0.3:
        gain_db -= (peak_db_after + 0.3)

    out = seg + gain_db
    buf = io.BytesIO()
    out.set_channels(1).export(buf, format="mp3", bitrate=MP3_BITRATE)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Merged-audio concat for poll output
# ---------------------------------------------------------------------------


_BEEP_CACHE: AudioSegment | None = None


def _beep_segment(
    duration_ms: int = 250, freq_hz: int = 800, fade_ms: int = 12,
) -> AudioSegment:
    """An 800 Hz sine pulse with short fades. IVR systems use this as the
    end-of-prompt cue. Generated once and cached for reuse."""
    global _BEEP_CACHE
    if _BEEP_CACHE is not None and len(_BEEP_CACHE) == duration_ms:
        return _BEEP_CACHE
    sample_rate = 24000
    n = int(sample_rate * duration_ms / 1000)
    t = np.arange(n) / sample_rate
    wave = (np.sin(2 * np.pi * freq_hz * t) * 0.7 * 32767).astype(np.int16)
    seg = AudioSegment(
        wave.tobytes(),
        frame_rate=sample_rate, sample_width=2, channels=1,
    ).fade_in(fade_ms).fade_out(fade_ms)
    _BEEP_CACHE = seg
    return seg


def concat_to_mp3(
    parts: list[Path],
    out_path: Path,
    *,
    gap_s: float = 1.0,
    include_beep: bool = True,
    gain_db: float = 0.0,
) -> Path:
    """Concatenate MP3 files at `parts` with `gap_s` of silence between each.
    Optionally appends a beep; applies a per-output gain in dB to the merged
    result. Writes one MP3 at `out_path` and returns its path.
    """
    if not parts:
        raise ValueError("concat_to_mp3: need at least one input")
    gap_ms = max(0, int(round(gap_s * 1000)))
    silence = AudioSegment.silent(duration=gap_ms) if gap_ms else None

    pieces: list[AudioSegment] = []
    for i, p in enumerate(parts):
        seg = AudioSegment.from_file(p).set_channels(1)
        if i > 0 and silence is not None:
            pieces.append(silence)
        pieces.append(seg)
    if include_beep:
        if silence is not None:
            pieces.append(silence)
        pieces.append(_beep_segment())

    merged = pieces[0]
    for seg in pieces[1:]:
        merged += seg

    if abs(gain_db) >= 0.05:
        merged = merged + float(gain_db)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.export(out_path, format="mp3", bitrate=MP3_BITRATE)
    return out_path
