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
    # English first — broadly understood, especially across the North-East
    # where Hindi reach drops off. en→en is a translator passthrough (no
    # model call); only the TTS adapter actually runs.
    "en":  LangSpec("en",  "English",     "eng_Latn", "Mary"),
    "hi":  LangSpec("hi",  "Hindi",       "hin_Deva", "Divya"),
    # The other ten "core" Indic languages with the strongest model coverage.
    "ta":  LangSpec("ta",  "Tamil",       "tam_Taml", "Jaya"),
    "te":  LangSpec("te",  "Telugu",      "tel_Telu", "Prakash"),
    "bn":  LangSpec("bn",  "Bengali",     "ben_Beng", "Arjun"),
    "mr":  LangSpec("mr",  "Marathi",     "mar_Deva", "Sanjay"),
    "kn":  LangSpec("kn",  "Kannada",     "kan_Knda", "Suresh"),
    "gu":  LangSpec("gu",  "Gujarati",    "guj_Gujr", "Yash"),
    "ml":  LangSpec("ml",  "Malayalam",   "mal_Mlym", "Anjali"),
    "or":  LangSpec("or",  "Odia",        "ory_Orya", "Manas"),
    "pa":  LangSpec("pa",  "Punjabi",     "pan_Guru", "Divjot"),
    # Additional languages supported end-to-end (IndicTrans2 + Indic Parler-TTS).
    # Quality varies — Punjabi, Kashmiri are flagged "unofficial" by Parler.
    "as":  LangSpec("as",  "Assamese",    "asm_Beng", "Amit"),
    "ur":  LangSpec("ur",  "Urdu",        "urd_Arab", "Aman"),
    "ne":  LangSpec("ne",  "Nepali",      "npi_Deva", "Amrita"),
    "sa":  LangSpec("sa",  "Sanskrit",    "san_Deva", "Aryan"),
    "mai": LangSpec("mai", "Maithili",    "mai_Deva", "Aman"),
    "ks":  LangSpec("ks",  "Kashmiri",    "kas_Arab", "Aman"),
    "sd":  LangSpec("sd",  "Sindhi",      "snd_Deva", "Aman"),
    "brx": LangSpec("brx", "Bodo",        "brx_Deva", "Bikram"),
    "doi": LangSpec("doi", "Dogri",       "doi_Deva", "Karan"),
    "kok": LangSpec("kok", "Konkani",     "gom_Deva", "Sanjay"),
    "mni": LangSpec("mni", "Manipuri",    "mni_Beng", "Laishram"),
    "sat": LangSpec("sat", "Santali",     "sat_Olck", "Manas"),
}

ALL_LANG_CODES = list(LANGUAGES.keys())

# What new projects get by default. Two languages keeps the editor
# scannable; users opt in to the rest from project settings.
DEFAULT_PROJECT_LANGS: list[str] = ["en", "hi"]

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
