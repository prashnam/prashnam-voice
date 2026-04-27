from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


TRANSLATION_MODEL = "ai4bharat/indictrans2-en-indic-dist-200M"
TTS_MODEL = "ai4bharat/indic-parler-tts"

CACHE_DIR = Path.home() / ".cache" / "prashnam-voice"
AUDIO_CACHE_DIR = CACHE_DIR / "audio"


@dataclass(frozen=True)
class LangSpec:
    code: str
    name: str
    it2: str           # IndicTrans2 / FLORES tag
    voice: str         # default Parler-TTS speaker name


LANGUAGES: dict[str, LangSpec] = {
    # English first — broadly understood across India incl. the North-East,
    # where Hindi/Indic-language reach drops off. en→en is a translator
    # passthrough (no model call); only the TTS adapter actually runs.
    "en": LangSpec("en", "English",   "eng_Latn", "Aditi"),
    "hi": LangSpec("hi", "Hindi",     "hin_Deva", "Divya"),
    "ta": LangSpec("ta", "Tamil",     "tam_Taml", "Jaya"),
    "te": LangSpec("te", "Telugu",    "tel_Telu", "Prakash"),
    "bn": LangSpec("bn", "Bengali",   "ben_Beng", "Arjun"),
    "mr": LangSpec("mr", "Marathi",   "mar_Deva", "Sanjay"),
    "kn": LangSpec("kn", "Kannada",   "kan_Knda", "Suresh"),
    "gu": LangSpec("gu", "Gujarati",  "guj_Gujr", "Yash"),
    "pa": LangSpec("pa", "Punjabi",   "pan_Guru", "Divya"),
    "ml": LangSpec("ml", "Malayalam", "mal_Mlym", "Anjali"),
    "or": LangSpec("or", "Odia",      "ory_Orya", "Manas"),
}

ALL_LANG_CODES = list(LANGUAGES.keys())

PACE_PHRASES: dict[str, str] = {
    "very_slow": "speaks very slowly",
    "slow":      "speaks slowly",
    "moderate":  "speaks at a moderate pace",
    "fast":      "speaks quickly",
    "very_fast": "speaks very quickly",
}
DEFAULT_PACE = "moderate"

DEFAULT_VOICE_DESCRIPTION_TEMPLATE = (
    "{voice} {pace_phrase} in a clear, warm voice. "
    "The recording is of very high quality, with no background noise."
)


def pace_phrase(pace: str) -> str:
    if pace not in PACE_PHRASES:
        raise ValueError(
            f"Unknown pace {pace!r}. Choose one of: {', '.join(PACE_PHRASES)}"
        )
    return PACE_PHRASES[pace]


def parse_langs(spec: str) -> list[str]:
    if not spec or spec.strip().lower() == "all":
        return list(ALL_LANG_CODES)
    out: list[str] = []
    seen: set[str] = set()
    for raw in spec.split(","):
        code = raw.strip().lower()
        if not code:
            continue
        if code not in LANGUAGES:
            raise ValueError(
                f"Unsupported language code: {code!r}. "
                f"Supported: {', '.join(ALL_LANG_CODES)}"
            )
        if code not in seen:
            out.append(code)
            seen.add(code)
    if not out:
        raise ValueError("No languages specified.")
    return out
