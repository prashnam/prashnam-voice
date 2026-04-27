"""Text normalization for TTS.

Indic Parler-TTS (and most multilingual TTS models) mispronounce or skip
numeric digits in Hindi / Marathi / other Indic scripts. They expect
spelled-out words. IndicTrans2 doesn't auto-spell numerals when translating,
so digits survive into the target text and reach the TTS as gibberish.

The fix that's cheap and works for every adapter: convert standalone
numerals in the *English* source to English words. The translator then
picks the natural target-language wording (e.g. "one" → "एक"), which the
TTS reads correctly.

Examples
    "press 1"        → "press one"
    "in 2026"        → "in two thousand and twenty-six"
    "10,000 voters"  → "ten thousand voters"
    "BJP123 alpha"   → "BJP123 alpha"   (alphanumeric stays untouched)
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

try:
    from num2words import num2words as _n2w  # type: ignore
except Exception:  # noqa: BLE001 — graceful fallback
    _n2w = None
    log.warning("num2words not installed; numerals will not be normalized")


# Match a standalone integer or decimal: 1 / 23 / 2026 / 1,000 / 1.5
# Word boundaries on each side skip alphanumerics like "BJP123" or "Q1".
_NUM = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\b")


def numerals_to_words(text: str, lang: str = "en") -> str:
    """Replace standalone numerals in `text` with their word form.

    Defaults to English so the conversion happens before translation. Pass
    a different `lang` only if you want to normalize in a target script
    (rarely needed; we normalize at the source).
    """
    if not text or _n2w is None:
        return text

    def _repl(m: re.Match) -> str:
        token = m.group(1).replace(",", "")
        try:
            n = float(token) if "." in token else int(token)
            return _n2w(n, lang=lang)
        except (ValueError, NotImplementedError, OverflowError):
            return m.group(0)

    return _NUM.sub(_repl, text)
