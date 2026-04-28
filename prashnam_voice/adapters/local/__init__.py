"""Local adapter — wraps the on-device AI4Bharat models.

Translator: naklitechie/indictrans2-en-indic-dist-200M  (mirror of ai4bharat/…, MIT)
TTS:        naklitechie/indic-parler-tts                 (mirror of ai4bharat/…, Apache-2.0)

We pull from public ungated mirrors so first-run install needs no HF
account or token. Bytes are byte-identical to the upstream AI4Bharat
repos; see each mirror's NOTICE.md for provenance.

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
