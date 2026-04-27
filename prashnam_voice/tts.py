from __future__ import annotations

import gc
import logging
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from .config import (
    DEFAULT_PACE,
    DEFAULT_VOICE_DESCRIPTION_TEMPLATE,
    LANGUAGES,
    TTS_MODEL,
    pace_phrase,
)

log = logging.getLogger(__name__)


def _select_device_dtype() -> tuple[str, torch.dtype]:
    if torch.cuda.is_available():
        return "cuda", torch.float16
    if torch.backends.mps.is_available():
        return "mps", torch.float16
    return "cpu", torch.float32


# ~10 s of audio at 87 frames/sec — caps the rare runaway where sampling
# fails to emit EOS. Normal poll clips terminate naturally at 1-3 s.
MAX_AUDIO_TOKENS = 900
# Floor to prevent the sampler from picking EOS on the first step — without
# this, very short Indic prompts (e.g. "Party B" → "கட்சி பி") sometimes
# produce a 1-sample waveform.
MIN_AUDIO_TOKENS = 30
# Minimum audio samples we'll accept; anything shorter than ~50 ms is treated
# as a failed generation and retried.
MIN_AUDIO_SAMPLES = 2200
MAX_RETRIES = 3


def _voice_description(voice: str, pace: str = DEFAULT_PACE) -> str:
    return DEFAULT_VOICE_DESCRIPTION_TEMPLATE.format(
        voice=voice, pace_phrase=pace_phrase(pace),
    )


class TTS:
    """Indic Parler-TTS wrapper.

    Loads weights once, synthesizes WAV for arbitrary (text, lang) pairs.
    Auto-falls back to CPU if MPS errors during a startup probe.
    """

    def __init__(self, model_id: str = TTS_MODEL, force_cpu: bool = False):
        from parler_tts import ParlerTTSForConditionalGeneration  # lazy
        from transformers import AutoTokenizer

        self.model_id = model_id
        if force_cpu:
            self.device, self.dtype = "cpu", torch.float32
        else:
            self.device, self.dtype = _select_device_dtype()
        log.info("Loading TTS %s on %s (%s)", model_id, self.device, self.dtype)

        self.model = ParlerTTSForConditionalGeneration.from_pretrained(
            model_id, torch_dtype=self.dtype
        ).to(self.device)
        self.model.eval()

        # Two tokenizers: the description (English voice prompt) goes through the
        # text encoder's tokenizer (typically flan-t5); the actual text-to-speak
        # uses the model's own tokenizer, which knows Indic scripts.
        text_encoder_name = self.model.config.text_encoder._name_or_path
        self.tokenizer = AutoTokenizer.from_pretrained(text_encoder_name)
        self.prompt_tokenizer = AutoTokenizer.from_pretrained(model_id)

        self.sampling_rate = int(self.model.config.sampling_rate)

        if self.device == "mps":
            self._probe_or_fallback()

    def _probe_or_fallback(self) -> None:
        try:
            self._synthesize("नमस्ते", "Divya", DEFAULT_PACE)
            log.info("MPS probe passed.")
        except Exception as exc:  # noqa: BLE001 — we want any failure to trigger fallback
            log.warning("MPS probe failed (%s); falling back to CPU.", exc)
            self.model = self.model.to("cpu")
            self.device = "cpu"

    @torch.inference_mode()
    def _synthesize(self, text: str, voice: str, pace: str) -> np.ndarray:
        description = _voice_description(voice, pace)
        desc_in = self.tokenizer(description, return_tensors="pt").to(self.device)
        prompt_in = self.prompt_tokenizer(text, return_tensors="pt").to(self.device)

        last_len = 0
        for attempt in range(1, MAX_RETRIES + 1):
            gen = self.model.generate(
                input_ids=desc_in.input_ids,
                attention_mask=desc_in.attention_mask,
                prompt_input_ids=prompt_in.input_ids,
                prompt_attention_mask=prompt_in.attention_mask,
                do_sample=True,
                min_new_tokens=MIN_AUDIO_TOKENS,
                max_new_tokens=MAX_AUDIO_TOKENS,
            )
            audio = gen.detach().to("cpu").to(torch.float32).numpy().reshape(-1)
            last_len = audio.size
            if last_len >= MIN_AUDIO_SAMPLES:
                return audio
            log.warning(
                "TTS attempt %d for %r yielded only %d samples; retrying.",
                attempt, text[:40], last_len,
            )
        raise RuntimeError(
            f"TTS failed to produce audible output for text={text!r} after "
            f"{MAX_RETRIES} attempts (last length {last_len} samples)."
        )

    def synthesize_to_wav(
        self,
        text: str,
        lang_code: str,
        out_path: Path,
        voice: str | None = None,
        pace: str = DEFAULT_PACE,
    ) -> Path:
        if lang_code not in LANGUAGES:
            raise ValueError(f"Unsupported language: {lang_code}")
        speaker = voice or LANGUAGES[lang_code].voice
        audio = self._synthesize(text, speaker, pace)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, audio, self.sampling_rate)
        return out_path

    def close(self) -> None:
        del self.model
        del self.tokenizer
        del self.prompt_tokenizer
        gc.collect()
        if self.device == "mps":
            torch.mps.empty_cache()
        elif self.device == "cuda":
            torch.cuda.empty_cache()
