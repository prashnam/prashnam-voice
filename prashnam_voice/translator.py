from __future__ import annotations

import gc
import logging
from typing import Iterable

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

from .config import LANGUAGES, TRANSLATION_MODEL

log = logging.getLogger(__name__)

SRC_LANG = "eng_Latn"


def _select_device() -> tuple[str, torch.dtype]:
    if torch.backends.mps.is_available():
        return "mps", torch.float16
    if torch.cuda.is_available():
        return "cuda", torch.float16
    return "cpu", torch.float32


class Translator:
    """Wraps IndicTrans2 + IndicProcessor.

    Translates English text into one Indic language at a time, in batches.
    """

    def __init__(self, model_id: str = TRANSLATION_MODEL):
        from IndicTransToolkit.processor import IndicProcessor  # lazy

        self.model_id = model_id
        self.device, self.dtype = _select_device()
        log.info("Loading translator %s on %s (%s)", model_id, self.device, self.dtype)
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            model_id, trust_remote_code=True, torch_dtype=self.dtype
        ).to(self.device)
        self.model.eval()
        self.processor = IndicProcessor(inference=True)

    @torch.inference_mode()
    def translate_batch(self, texts: list[str], lang_code: str) -> list[str]:
        if lang_code not in LANGUAGES:
            raise ValueError(f"Unsupported language: {lang_code}")
        if not texts:
            return []
        # English source → English target is a no-op. Bypass the model so we
        # don't burn a model load on a language pair IndicTrans2 isn't built
        # for, and so users can keep an "English" output column for free.
        if lang_code == "en":
            return list(texts)
        tgt_lang = LANGUAGES[lang_code].it2
        prepared = self.processor.preprocess_batch(texts, src_lang=SRC_LANG, tgt_lang=tgt_lang)
        inputs = self.tokenizer(
            prepared,
            truncation=True,
            padding="longest",
            return_tensors="pt",
            return_attention_mask=True,
        ).to(self.device)
        gen = self.model.generate(
            **inputs,
            max_length=256,
            num_beams=5,
            num_return_sequences=1,
            early_stopping=True,
        )
        with self.tokenizer.as_target_tokenizer():
            decoded = self.tokenizer.batch_decode(
                gen.detach().cpu().tolist(),
                skip_special_tokens=True,
                clean_up_tokenization_spaces=True,
            )
        return self.processor.postprocess_batch(decoded, lang=tgt_lang)

    def translate_many(
        self, texts: list[str], lang_codes: Iterable[str]
    ) -> dict[str, list[str]]:
        return {code: self.translate_batch(texts, code) for code in lang_codes}

    def close(self) -> None:
        del self.model
        del self.tokenizer
        del self.processor
        gc.collect()
        if self.device == "mps":
            torch.mps.empty_cache()
        elif self.device == "cuda":
            torch.cuda.empty_cache()
