"""Helpers for the in-app onboarding wizard.

Two groups of functionality:
  * probe_sarvam_key — connection test during onboarding
  * Model download tracker — drives the "Download models" step with
    byte-level progress reported back to the wizard.

The HF token / ToS probes that used to live here are gone — we now pull
the AI4Bharat models from public ungated mirrors at naklitechie/*, so
there's nothing for the user to authenticate against.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import requests

from .config import TRANSLATION_MODEL, TTS_MODEL

log = logging.getLogger(__name__)

SARVAM_API_BASE = "https://api.sarvam.ai"
HTTP_TIMEOUT = 15


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


# ---------------------------------------------------------------------------
# Model download tracker
# ---------------------------------------------------------------------------
#
# `huggingface_hub.snapshot_download` does the actual fetching. To surface
# byte-level progress we don't try to instrument tqdm — we kick off the
# download in a thread, then watch the HF cache directory's size in a
# parallel poller. Cheap and reliable.


@dataclass
class ModelProgress:
    model_id: str
    total_bytes: int = 0
    downloaded_bytes: int = 0
    status: Literal["queued", "running", "done", "error"] = "queued"
    error: str | None = None


@dataclass
class DownloadJob:
    state: Literal["idle", "running", "done", "error"] = "idle"
    models: dict[str, ModelProgress] = field(default_factory=dict)
    error: str | None = None
    started_at: float = 0.0
    finished_at: float = 0.0


# Single-flight: only one model download runs at a time across the whole
# process. The wizard only ever fires one anyway.
_download_lock = threading.Lock()
_download_job: DownloadJob = DownloadJob()


def get_download_progress() -> DownloadJob:
    return _download_job


def _hf_cache_dir(model_id: str) -> Path:
    """The path huggingface_hub.snapshot_download writes into."""
    safe = model_id.replace("/", "--")
    return Path.home() / ".cache" / "huggingface" / "hub" / f"models--{safe}"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _is_already_cached(model_id: str) -> bool:
    """Reasonable heuristic that the model is fully present locally."""
    snapshots = _hf_cache_dir(model_id) / "snapshots"
    if not snapshots.exists():
        return False
    # Any snapshot dir with at least one file inside.
    for sub in snapshots.iterdir():
        if sub.is_dir() and any(sub.rglob("*")):
            return True
    return False


def start_model_download(token: str | None) -> bool:
    """Kick off both model downloads in a background thread. Returns True
    if a new job started; False if one was already running."""
    global _download_job
    with _download_lock:
        if _download_job.state == "running":
            return False
        _download_job = DownloadJob(state="running", started_at=time.time())
        for mid in (TRANSLATION_MODEL, TTS_MODEL):
            _download_job.models[mid] = ModelProgress(model_id=mid)
        threading.Thread(
            target=_run_downloads, args=(token,), daemon=True,
        ).start()
        return True


def _resolve_total(model_id: str, token: str | None) -> int:
    """Fetch the total download size from HF Hub. If the API call fails
    (offline, rate-limit), fall back to a sensible estimate so the
    progress bar still moves."""
    fallback = {
        "naklitechie/indictrans2-en-indic-dist-200M": 1_100 * 1024 * 1024,
        "naklitechie/indic-parler-tts": 3_750 * 1024 * 1024,
        # Legacy keys kept so a stale config still gets a reasonable
        # progress bar if the user hasn't pulled the mirror swap.
        "ai4bharat/indictrans2-en-indic-dist-200M": 800 * 1024 * 1024,
        "ai4bharat/indic-parler-tts": 3_600 * 1024 * 1024,
    }.get(model_id, 0)
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token) if token else HfApi()
        info = api.model_info(model_id, files_metadata=True)
        total = sum((s.size or 0) for s in info.siblings)
        return total or fallback
    except Exception as exc:  # noqa: BLE001
        log.warning("could not fetch %s metadata: %s; falling back", model_id, exc)
        return fallback


def _run_downloads(token: str | None) -> None:
    job = _download_job
    try:
        for mid in (TRANSLATION_MODEL, TTS_MODEL):
            progress = job.models[mid]
            cache = _hf_cache_dir(mid)
            progress.total_bytes = _resolve_total(mid, token)

            if _is_already_cached(mid):
                progress.downloaded_bytes = progress.total_bytes
                progress.status = "done"
                continue

            progress.status = "running"

            # Watcher polls the cache dir size and updates downloaded_bytes
            # while the actual download runs.
            stop = threading.Event()

            def _watch(p: ModelProgress = progress, c: Path = cache, e: threading.Event = stop) -> None:
                while not e.is_set():
                    p.downloaded_bytes = _dir_size(c)
                    time.sleep(0.5)
                p.downloaded_bytes = _dir_size(c)

            t = threading.Thread(target=_watch, daemon=True)
            t.start()

            try:
                # Set HF token via env so snapshot_download picks it up.
                # Honor an existing env var (e.g. huggingface-cli login).
                if token:
                    os.environ.setdefault("HF_TOKEN", token)
                    os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)
                from huggingface_hub import snapshot_download
                snapshot_download(repo_id=mid, repo_type="model", token=token)
            except Exception as exc:  # noqa: BLE001
                progress.status = "error"
                progress.error = f"{type(exc).__name__}: {exc}"
                stop.set()
                t.join(timeout=2)
                job.state = "error"
                job.error = progress.error
                job.finished_at = time.time()
                return

            stop.set()
            t.join(timeout=2)
            progress.downloaded_bytes = max(progress.downloaded_bytes, progress.total_bytes)
            progress.status = "done"

        job.state = "done"
    except Exception as exc:  # noqa: BLE001
        job.state = "error"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished_at = time.time()
