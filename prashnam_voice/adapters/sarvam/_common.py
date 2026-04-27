"""Shared bits for Sarvam translator + TTS adapters."""
from __future__ import annotations

# Map our internal language codes to Sarvam's BCP-47 codes.
# Our `or` (Odia) → Sarvam's `od-IN` (note: not `or-IN`).
LANG_MAP: dict[str, str] = {
    "en":  "en-IN",
    "hi":  "hi-IN",
    "ta":  "ta-IN",
    "te":  "te-IN",
    "bn":  "bn-IN",
    "mr":  "mr-IN",
    "kn":  "kn-IN",
    "gu":  "gu-IN",
    "pa":  "pa-IN",
    "ml":  "ml-IN",
    "or":  "od-IN",
    # Sarvam translate covers all 22 scheduled languages of India + English.
    # TTS coverage is narrower (currently the 11 above) — translate-only
    # languages may error on synthesize; the adapter surfaces the error
    # so the user can switch to local for those.
    "as":  "as-IN",
    "ur":  "ur-IN",
    "ne":  "ne-IN",
    "sa":  "sa-IN",
    "mai": "mai-IN",
    "ks":  "ks-IN",
    "sd":  "sd-IN",
    "brx": "brx-IN",
    "doi": "doi-IN",
    "kok": "kok-IN",
    "mni": "mni-IN",
    "sat": "sat-IN",
}

SUPPORTED_LANGS = sorted(LANG_MAP.keys())

API_BASE = "https://api.sarvam.ai"
TIMEOUT_S = 60


def auth_header(api_key: str) -> dict[str, str]:
    return {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }
