"""Helpers for the in-app onboarding wizard.

Two test entrypoints:
  * probe_hf_token(token)       — checks Hugging Face access for both gated
                                 models (IndicTrans2 + Indic Parler-TTS).
  * probe_sarvam_key(api_key)   — runs a no-op translate against Sarvam.

Each returns a structured result the frontend uses to render success/failure
and tell the user *why* it failed (bad token vs. ToS not accepted vs.
network error).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import requests

from .config import TRANSLATION_MODEL, TTS_MODEL

log = logging.getLogger(__name__)

HF_API_BASE = "https://huggingface.co/api"
SARVAM_API_BASE = "https://api.sarvam.ai"
HTTP_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Hugging Face — token + per-model gating probe
# ---------------------------------------------------------------------------


@dataclass
class HfModelStatus:
    model_id: str
    status: Literal["ok", "needs_acceptance", "bad_token", "error"]
    detail: str = ""


@dataclass
class HfProbeResult:
    overall: Literal["ready", "token_invalid", "models_not_accepted", "error"]
    models: list[HfModelStatus]
    message: str = ""


HF_REQUIRED_MODELS = (TRANSLATION_MODEL, TTS_MODEL)


def probe_hf_token(token: str) -> HfProbeResult:
    """Probe HF Hub for both gated models we need.

    The model `siblings` endpoint is gated the same way as the weights:
        200  → token valid + ToS accepted
        401  → token invalid / missing
        403  → token valid but ToS not yet accepted for this model
    """
    if not token or not token.strip():
        return HfProbeResult(
            overall="token_invalid",
            models=[],
            message="Token is empty.",
        )

    headers = {"Authorization": f"Bearer {token.strip()}"}
    statuses: list[HfModelStatus] = []
    bad_token = False
    not_accepted: list[str] = []

    for model_id in HF_REQUIRED_MODELS:
        url = f"{HF_API_BASE}/models/{model_id}"
        try:
            r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            statuses.append(HfModelStatus(model_id, "error", f"network: {exc}"))
            continue

        if r.status_code == 200:
            statuses.append(HfModelStatus(model_id, "ok"))
        elif r.status_code == 401:
            bad_token = True
            statuses.append(HfModelStatus(
                model_id, "bad_token", "token rejected"
            ))
        elif r.status_code == 403:
            not_accepted.append(model_id)
            statuses.append(HfModelStatus(
                model_id, "needs_acceptance",
                "open the model page on huggingface.co and click 'Agree and access'",
            ))
        else:
            statuses.append(HfModelStatus(
                model_id, "error", f"HTTP {r.status_code}"
            ))

    if bad_token:
        return HfProbeResult(
            overall="token_invalid",
            models=statuses,
            message="The token was rejected. Generate a new read token.",
        )
    if not_accepted:
        return HfProbeResult(
            overall="models_not_accepted",
            models=statuses,
            message=(
                "Token works but you still need to accept the licence on: "
                + ", ".join(not_accepted)
            ),
        )
    if all(s.status == "ok" for s in statuses):
        return HfProbeResult(
            overall="ready",
            models=statuses,
            message="HF token works for both models.",
        )
    return HfProbeResult(
        overall="error",
        models=statuses,
        message="Something went wrong — see model details.",
    )


# ---------------------------------------------------------------------------
# Sarvam — minimal translate probe
# ---------------------------------------------------------------------------


@dataclass
class SarvamProbeResult:
    overall: Literal["ready", "key_invalid", "quota", "error"]
    message: str = ""
    sample: str = ""    # the translated sample if successful


def probe_sarvam_key(api_key: str) -> SarvamProbeResult:
    """Translate "hello" → Hindi as a smoke test.

    Cheapest possible call (single short string) so it's safe to run on every
    keystroke if the UI ever wants live validation.
    """
    if not api_key or not api_key.strip():
        return SarvamProbeResult(overall="key_invalid", message="Key is empty.")

    body = {
        "input": "hello",
        "source_language_code": "en-IN",
        "target_language_code": "hi-IN",
        "model": "sarvam-translate:v1",
        "mode": "formal",
    }
    headers = {
        "api-subscription-key": api_key.strip(),
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            f"{SARVAM_API_BASE}/translate",
            json=body,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        return SarvamProbeResult(overall="error", message=f"network: {exc}")

    if r.status_code in (401, 403):
        return SarvamProbeResult(
            overall="key_invalid",
            message="API key rejected. Check it's the read key from dashboard.sarvam.ai.",
        )
    if r.status_code == 429:
        return SarvamProbeResult(
            overall="quota",
            message="Rate limited or quota exceeded.",
        )
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = err.get("message") or r.text[:200]
        except Exception:  # noqa: BLE001
            msg = r.text[:200]
        return SarvamProbeResult(
            overall="error",
            message=f"HTTP {r.status_code}: {msg}",
        )
    try:
        translated = r.json().get("translated_text") or ""
    except Exception:  # noqa: BLE001
        translated = ""
    return SarvamProbeResult(
        overall="ready",
        message="Sarvam connection works.",
        sample=translated,
    )
