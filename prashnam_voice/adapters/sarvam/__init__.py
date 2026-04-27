"""Sarvam.ai cloud adapter — translation + TTS over their REST API.

Single API key from https://dashboard.sarvam.ai gates both endpoints.
The same key is used for both translator and TTS — we expose them as
two adapters so users can mix (e.g. local translate + Sarvam TTS).
"""
from __future__ import annotations

from .translator import SarvamTranslator
from .tts import SarvamTTS

from .. import register_translator, register_tts

register_translator(SarvamTranslator())
register_tts(SarvamTTS())
