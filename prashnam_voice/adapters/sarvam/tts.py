"""Sarvam TTS adapter — POST https://api.sarvam.ai/text-to-speech.

The endpoint can return MP3 directly (`output_audio_codec=mp3`), so no
ffmpeg roundtrip needed. Pace is mapped from our 5-step enum to Sarvam's
numeric `pace` (0.5–2.0).
"""
from __future__ import annotations

import base64
import logging

import requests

from ..base import AdapterError, Setting, Voice
from ._common import API_BASE, LANG_MAP, SUPPORTED_LANGS, TIMEOUT_S, auth_header

log = logging.getLogger(__name__)

# bulbul:v3 default speaker pool; case-sensitive lowercase per Sarvam docs.
# All voices work for all supported langs (Sarvam's voice ↔ language pairing
# isn't constrained at the API level).
V3_SPEAKERS = [
    "shubh", "aditya", "aayan", "advait", "amit", "anand", "ashutosh",
    "dev", "gokul", "ishita", "kabir", "kavita", "kavya", "mani", "manan",
    "mohit", "pooja", "priya", "rahul", "ratan", "rehan", "rohan", "roopa",
    "rupali", "shreya", "shruti", "simran", "soham", "suhani", "sumit",
    "sunny", "tarun", "tanya", "varun", "vijay",
]
DEFAULT_SPEAKER = "shubh"

# Map our 5-step pace enum onto Sarvam's continuous parameter (0.5–2.0).
PACE_TO_NUMERIC: dict[str, float] = {
    "very_slow": 0.6,
    "slow":      0.8,
    "moderate":  1.0,
    "fast":      1.25,
    "very_fast": 1.6,
}


class SarvamTTS:
    name = "sarvam"
    label = "Sarvam.ai (bulbul-v3)"
    description = (
        "Indic-first cloud TTS. Sounds noticeably more natural for "
        "Indian languages than most generic engines. Needs an API key."
    )
    supports_offline = False
    supported_langs = list(SUPPORTED_LANGS)
    needs_setup = [
        Setting(
            key="api_key",
            label="API key",
            help="Same key works for translate + TTS. Get one at dashboard.sarvam.ai.",
            secret=True,
            url="https://dashboard.sarvam.ai",
        ),
    ]

    def is_configured(self, cfg: dict) -> bool:
        return bool((cfg or {}).get("api_key"))

    def voices_for(self, lang: str, cfg: dict) -> list[Voice]:
        if lang not in LANG_MAP:
            return []
        # Voice ↔ language is not constrained by Sarvam, but humans pick
        # better voices when grouped sensibly. Just return everything.
        return [Voice(id=s, name=s, lang=lang) for s in V3_SPEAKERS]

    def synthesize(
        self,
        text: str,
        lang: str,
        voice: str,
        pace: str,
        cfg: dict,
    ) -> bytes:
        if not self.is_configured(cfg):
            raise AdapterError(self.name, "API key missing", setup_required=True)
        if lang not in LANG_MAP:
            raise AdapterError(self.name, f"unsupported language: {lang}")

        api_key = cfg["api_key"]
        speaker = (voice or DEFAULT_SPEAKER).strip().lower()
        if speaker not in V3_SPEAKERS:
            log.info("sarvam: unknown speaker %r, falling back to %s",
                     speaker, DEFAULT_SPEAKER)
            speaker = DEFAULT_SPEAKER

        body = {
            "text": text,
            "target_language_code": LANG_MAP[lang],
            "speaker": speaker,
            "model": "bulbul:v3",
            "pace": PACE_TO_NUMERIC.get(pace, 1.0),
            "output_audio_codec": "mp3",
            "speech_sample_rate": "22050",
        }
        try:
            r = requests.post(
                f"{API_BASE}/text-to-speech",
                json=body,
                headers=auth_header(api_key),
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise AdapterError(self.name, f"network error: {exc}") from exc

        if r.status_code in (401, 403):
            raise AdapterError(
                self.name, "API key rejected — check your key in settings.",
                setup_required=True,
            )
        if r.status_code == 429:
            raise AdapterError(self.name, "rate limit / quota exceeded")
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
                msg = err.get("message") or r.text[:200]
            except Exception:  # noqa: BLE001
                msg = r.text[:200]
            raise AdapterError(self.name, f"HTTP {r.status_code}: {msg}")

        try:
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(self.name, f"bad JSON in response: {exc}") from exc

        audios = payload.get("audios") or []
        if not audios:
            raise AdapterError(self.name, f"empty audios array: {payload}")
        try:
            return base64.b64decode(audios[0])
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(self.name, f"bad base64 in response: {exc}") from exc

    def close(self) -> None:
        pass  # stateless
