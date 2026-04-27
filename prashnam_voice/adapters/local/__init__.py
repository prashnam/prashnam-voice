"""Local adapter — wraps the on-device AI4Bharat models.

Translator: ai4bharat/indictrans2-en-indic-dist-200M
TTS:        ai4bharat/indic-parler-tts

Both run on Apple Silicon MPS in fp16 (with auto-fallback to CPU on
probe failure). Models download lazily on first use to ~/.cache/huggingface.
"""
from __future__ import annotations

from .translator import LocalTranslator
from .tts import LocalTTS

# Self-register at import time. The registry's ensure_builtins_loaded()
# imports this module, which causes the registrations below to fire.
from .. import register_translator, register_tts

register_translator(LocalTranslator())
register_tts(LocalTTS())
