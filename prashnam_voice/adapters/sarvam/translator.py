"""Sarvam translator adapter — POST https://api.sarvam.ai/translate.

Sarvam doesn't expose a batch endpoint, so we issue one request per text.
Concurrency: kept sequential to stay under their rate limits and avoid
double-charging on retries. Translation is fast (typically <1 s per call)
so the loop is fine for poll-sized inputs.
"""
from __future__ import annotations

import logging

import requests

from ..base import AdapterError, Setting
from ._common import API_BASE, LANG_MAP, SUPPORTED_LANGS, TIMEOUT_S, auth_header

log = logging.getLogger(__name__)


class SarvamTranslator:
    name = "sarvam"
    label = "Sarvam.ai"
    description = (
        "Indic-first cloud translation. Pay-per-call, "
        "best-in-class for Indian languages. Needs an API key."
    )
    supports_offline = False
    supported_langs = list(SUPPORTED_LANGS)
    needs_setup = [
        Setting(
            key="api_key",
            label="API key",
            help="Get one at dashboard.sarvam.ai → API keys → Create new key.",
            secret=True,
            url="https://dashboard.sarvam.ai",
        ),
    ]

    def is_configured(self, cfg: dict) -> bool:
        return bool((cfg or {}).get("api_key"))

    def translate_batch(
        self, texts: list[str], lang: str, cfg: dict
    ) -> list[str]:
        if not self.is_configured(cfg):
            raise AdapterError(self.name, "API key missing", setup_required=True)
        if lang not in LANG_MAP:
            raise AdapterError(self.name, f"unsupported language: {lang}")
        if not texts:
            return []
        # English source → English target is a no-op; don't bill a translate call.
        if lang == "en":
            return list(texts)

        api_key = cfg["api_key"]
        target = LANG_MAP[lang]
        out: list[str] = []
        for text in texts:
            translated = self._translate_one(text, target, api_key)
            out.append(translated)
        return out

    def _translate_one(self, text: str, target_bcp47: str, api_key: str) -> str:
        body = {
            "input": text,
            "source_language_code": "en-IN",
            "target_language_code": target_bcp47,
            "model": "sarvam-translate:v1",
            "mode": "formal",
        }
        try:
            r = requests.post(
                f"{API_BASE}/translate",
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

        if "translated_text" not in payload:
            raise AdapterError(self.name, f"no translated_text in response: {payload}")
        return payload["translated_text"] or ""

    def close(self) -> None:
        pass  # stateless
