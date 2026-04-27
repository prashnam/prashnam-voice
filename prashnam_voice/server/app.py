from __future__ import annotations

import io
import logging
import queue
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from .. import app_config
from .. import onboarding as onboarding_helpers
from .. import engines
from .. import domains as domains_mod

from ..config import (
    ALL_LANG_CODES,
    DEFAULT_PACE,
    LANGUAGES,
    PACE_PHRASES,
    parse_langs,
)
from ..pipeline import (
    JobProgress,
    LangProgress,
    run_pipeline,
    synthesize_segment_lang,
    translate_segments,
)
from ..projects import CANONICAL_ROTATION, ProjectStore

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# Project root (repo root): the top of the package's grandparent. The docs
# folder lives there; the server mounts it read-only so the in-app viewer
# can fetch markdown without going outside the project tree.
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"

# Curated whitelist + display order so we don't accidentally surface
# unrelated markdown that lands in /docs later.
DOC_INDEX = [
    {"file": "rest-api.md",   "title": "REST API",     "summary": "Every HTTP endpoint exposed by the running server."},
    {"file": "python-api.md", "title": "Python API",   "summary": "Embedding prashnam-voice in your own code."},
]
EXTRA_DOC_FILES = {
    "PLAN.md":             {"title": "PLAN",  "summary": "Tier 1 + Tier 2 milestones, design decisions, status."},
    "README.md":            {"title": "README", "summary": "Top-level project overview."},
    "guide/README.md":      {"title": "Guide", "summary": "Visual tour of the web app."},
}


class GenerateRequest(BaseModel):
    """Legacy one-shot endpoint payload (still used by the CLI)."""
    question: str
    options: list[str] = Field(min_length=1)
    langs: list[str] | None = None
    voices: dict[str, str] = Field(default_factory=dict)
    pace: str = DEFAULT_PACE
    paces: dict[str, str] = Field(default_factory=dict)


class TestHfRequest(BaseModel):
    token: str


class TestSarvamRequest(BaseModel):
    api_key: str


class CompleteOnboardingRequest(BaseModel):
    translator: str
    tts: str
    settings: dict[str, dict[str, str]] = Field(default_factory=dict)


class DownloadModelsRequest(BaseModel):
    token: str | None = None


class CreateProjectRequest(BaseModel):
    name: str
    langs: list[str] | None = None
    domain: str = "poll"


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    langs: list[str] | None = None
    default_pace: str | None = None
    voices: dict[str, str] | None = None
    paces: dict[str, str] | None = None
    question_template: str | None = None
    option_template: str | None = None
    lexicon: dict[str, dict[str, str]] | None = None


class AddSegmentRequest(BaseModel):
    type: str                              # "question" or "option"
    english: str = ""


class EditSegmentRequest(BaseModel):
    english: str


class EditSegmentTemplateRequest(BaseModel):
    use_template: bool


class EditSegmentOverrideRequest(BaseModel):
    """Set or clear a per-segment voice / pace override for one language.
    Send `voice` / `pace` to set; send `null` to clear; omit the field to
    leave the existing override untouched."""
    lang: str
    voice: str | None = None
    pace:  str | None = None


class RegenerateRequest(BaseModel):
    langs: list[str]                       # which languages to regenerate
    rotation_ids: list[str] | None = None  # which rotations; None = all active


class SelectTakeRequest(BaseModel):
    lang: str
    attempt_id: str
    rotation_id: str = "r0"


class EnableRotationsRequest(BaseModel):
    count: int
    seed: int | None = None
    lock_last_as_nota: bool = False


class ReshuffleRequest(BaseModel):
    seed: int | None = None


class ToggleLockRequest(BaseModel):
    lock_at_end: bool


def build_app(out_root: Path, projects_root: Path | None = None) -> FastAPI:
    out_root = out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    if projects_root is None:
        projects_root = out_root.parent / "projects"
    store = ProjectStore(projects_root.resolve())

    api = FastAPI(title="prashnam-voice", version=__version__)

    # CORS — the bootstrap index.html opens via file:// (origin `null`) and
    # needs to poll the health endpoint to detect when the server is up.
    # This server only ever binds to localhost, so wide-open CORS is safe.
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_origin_regex=r".*",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    jobs: dict[str, JobProgress] = {}
    jobs_lock = threading.Lock()
    # Insertion-ordered list of active job ids (queued + running) for the
    # right-side queue panel.
    job_order: list[str] = []

    # Two independent queues, one worker each.
    #   translate_queue  →  fast: one batch-translate per regen request, then
    #                      fans out N audio sub-tasks
    #   audio_queue      →  slow: one (segment, lang) synthesis per item, FIFO
    #                      across all regen requests
    translate_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
    audio_queue:     "queue.Queue[Callable[[], None]]" = queue.Queue()

    def _loop(q: "queue.Queue[Callable[[], None]]") -> Callable[[], None]:
        def run() -> None:
            while True:
                fn = q.get()
                try:
                    fn()
                except Exception:  # noqa: BLE001 — already logged inside the worker
                    log.exception("queued job raised")
                finally:
                    q.task_done()
        return run

    threading.Thread(target=_loop(translate_queue), daemon=True,
                     name="prashnam-translate").start()
    threading.Thread(target=_loop(audio_queue), daemon=True,
                     name="prashnam-audio").start()

    def _register_job(job_id: str, progress: JobProgress) -> None:
        with jobs_lock:
            jobs[job_id] = progress
            job_order.append(job_id)

    def _enqueue_audio(fn: Callable[[], None]) -> None:
        audio_queue.put(fn)

    # ------------------------------------------------------------------
    # Reference data
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Onboarding
    # ------------------------------------------------------------------

    @api.post("/api/onboarding/test-hf")
    def onboarding_test_hf(req: TestHfRequest) -> dict:
        from dataclasses import asdict
        result = onboarding_helpers.probe_hf_token(req.token)
        return {
            "overall": result.overall,
            "message": result.message,
            "models": [asdict(m) for m in result.models],
        }

    @api.post("/api/onboarding/test-sarvam")
    def onboarding_test_sarvam(req: TestSarvamRequest) -> dict:
        result = onboarding_helpers.probe_sarvam_key(req.api_key)
        return {
            "overall": result.overall,
            "message": result.message,
            "sample": result.sample,
        }

    @api.post("/api/onboarding/download-models")
    def onboarding_download_models(req: DownloadModelsRequest) -> dict:
        started = onboarding_helpers.start_model_download(req.token)
        return {"ok": True, "started": started}

    @api.get("/api/onboarding/download-progress")
    def onboarding_download_progress() -> dict:
        from dataclasses import asdict
        return asdict(onboarding_helpers.get_download_progress())

    @api.post("/api/onboarding/complete")
    def onboarding_complete(req: CompleteOnboardingRequest) -> dict:
        try:
            from .. import adapters as adapter_registry
            adapter_registry.get_translator(req.translator)
            adapter_registry.get_tts(req.tts)
        except KeyError as exc:
            raise HTTPException(400, f"unknown adapter: {exc}")

        def _apply(cfg: app_config.AppConfig) -> None:
            cfg.translator.name = req.translator
            cfg.tts.name = req.tts
            for adapter_name, settings in (req.settings or {}).items():
                cfg.translator.all_settings[adapter_name] = dict(settings)
                cfg.tts.all_settings[adapter_name] = dict(settings)
            cfg.onboarded = True

        app_config.update(_apply)
        # Drop any cached engine so subsequent calls pick up the new adapter.
        engines.release()
        return {"ok": True}

    # ------------------------------------------------------------------

    @api.get("/api/health")
    def health() -> dict:
        """Lightweight liveness + onboarding-state probe.

        Polled by the static bootstrap page (index.html opened via file://)
        to detect when the local server has started. Also surfaces whether
        the user has completed the in-app onboarding wizard, so the
        bootstrap page can route to /onboarding vs / accordingly.
        """
        cfg = app_config.load()
        return {
            "status": "ok",
            "version": __version__,
            "onboarded": cfg.onboarded,
            "translator": cfg.translator.name,
            "tts": cfg.tts.name,
        }

    @api.get("/api/languages")
    def list_languages() -> list[dict[str, str]]:
        return [
            {"code": c, "name": LANGUAGES[c].name, "voice": LANGUAGES[c].voice}
            for c in ALL_LANG_CODES
        ]

    @api.get("/api/paces")
    def list_paces() -> dict[str, list[str] | str]:
        return {"options": list(PACE_PHRASES.keys()), "default": DEFAULT_PACE}

    @api.get("/api/voices")
    def list_voices_all() -> dict[str, list[str]]:
        """Per-language voice pool from the active TTS adapter. Used by the
        per-segment override picker. Cheap — no model load required."""
        try:
            tts, cfg = engines.get_tts()
        except Exception:
            return {c: [LANGUAGES[c].voice] for c in ALL_LANG_CODES}
        out: dict[str, list[str]] = {}
        for code in ALL_LANG_CODES:
            try:
                voices = tts.voices_for(code, cfg) or []
            except Exception:
                voices = []
            ids = [v.id for v in voices] or [LANGUAGES[code].voice]
            out[code] = ids
        return out

    @api.get("/api/domains")
    def list_domains() -> list[dict]:
        return [d.to_json() for d in domains_mod.all_domains()]

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    @api.get("/api/projects")
    def list_projects() -> list[dict]:
        return store.list_projects()

    @api.post("/api/projects")
    def create_project(req: CreateProjectRequest) -> dict:
        if req.langs is not None:
            try:
                langs = parse_langs(",".join(req.langs)) if req.langs else None
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        else:
            langs = None
        try:
            proj = store.create(
                name=req.name or "Untitled",
                langs=langs,
                domain=req.domain or "poll",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return _project_payload(proj.id, store)

    @api.get("/api/projects/{pid}")
    def get_project(pid: str) -> dict:
        return _project_payload(pid, store)

    @api.patch("/api/projects/{pid}")
    def update_project(pid: str, req: UpdateProjectRequest) -> dict:
        try:
            store.update_settings(
                pid,
                name=req.name,
                langs=req.langs,
                default_pace=req.default_pace,
                voices=req.voices,
                paces=req.paces,
                question_template=req.question_template,
                option_template=req.option_template,
                lexicon=req.lexicon,
            )
        except FileNotFoundError:
            raise HTTPException(404, "project not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return _project_payload(pid, store)

    @api.post("/api/projects/import")
    async def import_projects_csv(
        file: UploadFile = File(...),
        domain: str = Form("poll"),
        langs: str = Form(""),
    ) -> dict:
        from .. import csv_import as csv_import_mod
        try:
            base_langs = parse_langs(langs) if langs else None
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        body = await file.read()
        try:
            result = csv_import_mod.import_csv(
                io.BytesIO(body),
                store,
                domain=domain,
                langs=base_langs,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {
            "created": [
                {"id": p.id, "name": p.name, "segments": len(p.segments)}
                for p in result.projects
            ],
            "rows_consumed": result.rows_consumed,
            "errors": [
                {"line": e.line_no, "message": e.message}
                for e in result.errors
            ],
        }

    @api.delete("/api/projects/{pid}")
    def delete_project(pid: str) -> dict:
        store.delete(pid)
        return {"deleted": pid}

    # ------------------------------------------------------------------
    # Segments
    # ------------------------------------------------------------------

    @api.post("/api/projects/{pid}/segments")
    def add_segment(pid: str, req: AddSegmentRequest) -> dict:
        try:
            proj = store.load(pid)
        except FileNotFoundError:
            raise HTTPException(404, "project not found")
        try:
            pack = domains_mod.get(proj.domain)
        except KeyError:
            raise HTTPException(500, f"unknown domain on project: {proj.domain}")
        spec = pack.segment_type(req.type)
        if spec is None:
            allowed = ", ".join(s.name for s in pack.segment_types)
            raise HTTPException(400, f"type {req.type!r} not valid in {pack.name} domain (allowed: {allowed})")
        if spec.max is not None:
            existing = sum(1 for s in proj.segments if s.type == req.type)
            if existing >= spec.max:
                raise HTTPException(400, f"only {spec.max} {req.type} allowed in this domain")
        try:
            seg = store.add_segment(pid, req.type, english=req.english)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"segment_id": seg.id, "project": _project_payload(pid, store)}

    @api.patch("/api/projects/{pid}/segments/{seg_id}")
    def edit_segment(pid: str, seg_id: str, req: EditSegmentRequest) -> dict:
        try:
            seg, invalidated = store.edit_segment_english(pid, seg_id, req.english)
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "project or segment not found")
        return {
            "segment": seg.to_json(),
            "invalidated_langs": invalidated,
        }

    @api.post("/api/projects/{pid}/rotations/enable")
    def enable_rotations(pid: str, req: EnableRotationsRequest) -> dict:
        if req.count < 2:
            raise HTTPException(400, "count must be >= 2 to enable rotations")
        try:
            store.enable_rotations(
                pid,
                count=req.count,
                seed=req.seed,
                lock_last_as_nota=req.lock_last_as_nota,
            )
        except FileNotFoundError:
            raise HTTPException(404, "project not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return _project_payload(pid, store)

    @api.post("/api/projects/{pid}/rotations/disable")
    def disable_rotations(pid: str) -> dict:
        try:
            store.disable_rotations(pid)
        except FileNotFoundError:
            raise HTTPException(404, "project not found")
        return _project_payload(pid, store)

    @api.post("/api/projects/{pid}/rotations/reshuffle")
    def reshuffle_rotations(pid: str, req: ReshuffleRequest) -> dict:
        try:
            store.reshuffle_rotations(pid, seed=req.seed)
        except FileNotFoundError:
            raise HTTPException(404, "project not found")
        return _project_payload(pid, store)

    @api.patch("/api/projects/{pid}/segments/{seg_id}/lock")
    def toggle_segment_lock(
        pid: str, seg_id: str, req: ToggleLockRequest,
    ) -> dict:
        try:
            seg = store.set_segment_lock_at_end(pid, seg_id, req.lock_at_end)
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "project or segment not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"segment": seg.to_json(), "project": _project_payload(pid, store)}

    @api.patch("/api/projects/{pid}/segments/{seg_id}/override")
    def edit_segment_override(
        pid: str, seg_id: str, req: EditSegmentOverrideRequest,
    ) -> dict:
        if req.lang not in LANGUAGES:
            raise HTTPException(400, f"unknown language: {req.lang}")
        # Pydantic's `model_fields_set` distinguishes "client sent the field"
        # (intent: set or clear) from "client omitted it" (intent: leave
        # existing override alone). Voice "" is treated the same as null.
        sent = req.model_fields_set
        voice_arg = None
        if "voice" in sent:
            v = req.voice or None
            voice_arg = (req.lang, v)
        pace_arg = None
        if "pace" in sent:
            p = req.pace or None
            pace_arg = (req.lang, p)
        if voice_arg is None and pace_arg is None:
            raise HTTPException(400, "supply 'voice' or 'pace' (or both)")
        try:
            seg = store.set_segment_overrides(
                pid, seg_id, voice=voice_arg, pace=pace_arg,
            )
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "project or segment not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"segment": seg.to_json()}

    @api.patch("/api/projects/{pid}/segments/{seg_id}/template")
    def edit_segment_template(
        pid: str, seg_id: str, req: EditSegmentTemplateRequest
    ) -> dict:
        try:
            seg = store.set_segment_use_template(pid, seg_id, req.use_template)
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "project or segment not found")
        return {"segment": seg.to_json()}

    @api.delete("/api/projects/{pid}/segments/{seg_id}")
    def delete_segment(pid: str, seg_id: str) -> dict:
        try:
            store.delete_segment(pid, seg_id)
        except FileNotFoundError:
            raise HTTPException(404, "project not found")
        return {"deleted": seg_id, "project": _project_payload(pid, store)}

    # ------------------------------------------------------------------
    # Regeneration jobs
    # ------------------------------------------------------------------

    @api.post("/api/projects/{pid}/segments/{seg_id}/regenerate")
    def regenerate(pid: str, seg_id: str, req: RegenerateRequest) -> dict:
        try:
            proj = store.load(pid)
        except FileNotFoundError:
            raise HTTPException(404, "project not found")
        try:
            seg_lookup = proj.find_segment(seg_id)
        except KeyError:
            raise HTTPException(404, "segment not found")

        bad = [c for c in req.langs if c not in LANGUAGES]
        if bad:
            raise HTTPException(400, f"unknown langs: {bad}")
        if not req.langs:
            raise HTTPException(400, "no langs specified")

        # Resolve target rotations. None = every active rotation.
        active = proj.rotation_ids()
        if req.rotation_ids:
            unknown = [r for r in req.rotation_ids if r not in active]
            if unknown:
                raise HTTPException(400, f"unknown rotation ids: {unknown}")
            rotation_ids = list(req.rotation_ids)
        else:
            rotation_ids = list(active)

        job_id = uuid.uuid4().hex[:12]
        langs = list(req.langs)
        progress = JobProgress()
        progress.project_id = pid
        progress.segment_id = seg_id
        # Question segments always live at r0. Options synth once per
        # active rotation. We aggregate progress into the existing
        # by_lang shape so the UI stays simple — `audio_total` is
        # the number of rotations we have to synthesize for this lang.
        is_option = bool(seg_lookup and seg_lookup.type == "option")
        effective_rotations = rotation_ids if is_option else [CANONICAL_ROTATION]
        R = len(effective_rotations)
        progress.by_lang = {c: LangProgress(audio_total=R) for c in langs}
        _register_job(job_id, progress)

        # Wall-clock start, captured when translation actually begins running
        # (not at submission). Single-element list so the inner closures can
        # mutate without `nonlocal`.
        started_at: list[float | None] = [None]

        # Translation phase — runs on the (fast) translate worker. When done it
        # fans out per-language audio sub-tasks onto the audio queue. So while
        # the audio worker is still grinding on an earlier job's synthesis,
        # later jobs' translations finish quickly and the user can audit text.
        def translate_phase() -> None:
            started_at[0] = time.time()
            progress.status = "running"
            try:
                proj = store.load(pid)
                seg = proj.find_segment(seg_id)
                # Translate per-rotation: each rotation has a different `{n}`
                # in the wrapped option text. For non-options, only r0 needs
                # translating (questions/bodies don't depend on rotation).
                for rid in effective_rotations:
                    missing = [
                        c for c in langs
                        if not (seg.translation_for(c, rid) or "")
                    ]
                    if missing:
                        translate_segments(proj, [seg], missing, rotation_id=rid)
                        store.update(proj)
                        # Re-load to pick up the persisted state.
                        proj = store.load(pid)
                        seg = proj.find_segment(seg_id)
                for c in langs:
                    progress.by_lang[c].translated = True
            except Exception as exc:
                log.exception("translate %s/%s failed: %s", pid, seg_id, exc)
                progress.status = "error"
                progress.error = f"translate: {type(exc).__name__}: {exc}"
                progress.elapsed_s = round(time.time() - (started_at[0] or time.time()), 2)
                return

            # Queue audio sub-tasks: one per (lang, rotation). FIFO across the
            # whole queue so an earlier job's clips finish before this one.
            for c in langs:
                for rid in effective_rotations:
                    _enqueue_audio(_make_audio_task(c, rid))

        def _make_audio_task(lang: str, rotation_id: str) -> Callable[[], None]:
            def synth() -> None:
                # First sibling task starts the lang's clock. audio_done counts
                # rotations completed; audio_total is len(effective_rotations).
                if not progress.by_lang[lang].audio_started:
                    progress.by_lang[lang].audio_started = True
                try:
                    proj = store.load(pid)
                    seg = proj.find_segment(seg_id)
                    att_id = synthesize_segment_lang(
                        store, proj, seg, lang, rotation_id=rotation_id,
                    )
                    store.set_current_take(pid, seg_id, lang, att_id, rotation_id=rotation_id)
                    progress.new_attempts[f"{lang}::{rotation_id}"] = att_id
                    progress.by_lang[lang].audio_done += 1
                except Exception as exc:
                    log.exception(
                        "synth %s/%s/%s/%s failed: %s",
                        pid, seg_id, lang, rotation_id, exc,
                    )
                    progress.error = f"{lang}/{rotation_id}: {type(exc).__name__}: {exc}"
                _maybe_finalize()
            return synth

        def _maybe_finalize() -> None:
            # A lang is "fully done" when all its rotation clips are produced.
            done_langs = [
                c for c, lp in progress.by_lang.items()
                if lp.audio_done >= lp.audio_total
            ]
            settled = [
                c for c, lp in progress.by_lang.items()
                if lp.audio_done >= lp.audio_total or lp.audio_started
            ]
            # Every lang has either reached its rotation total or started + failed.
            if len(settled) == len(progress.by_lang):
                # If a synth task already failed early, audio_started won't be
                # true for the not-yet-attempted langs. Make sure we still
                # close the job out instead of hanging.
                progress.status = "done" if done_langs else "error"
                progress.elapsed_s = round(
                    time.time() - (started_at[0] or time.time()), 2
                )

        translate_queue.put(translate_phase)
        return {"job_id": job_id}

    @api.post("/api/projects/{pid}/segments/{seg_id}/select")
    def select_take(pid: str, seg_id: str, req: SelectTakeRequest) -> dict:
        if req.lang not in LANGUAGES:
            raise HTTPException(400, f"unknown lang: {req.lang}")
        try:
            seg = store.set_current_take(
                pid, seg_id, req.lang, req.attempt_id,
                rotation_id=req.rotation_id or CANONICAL_ROTATION,
            )
        except FileNotFoundError:
            raise HTTPException(404, "project, segment or attempt not found")
        return {"segment": seg.to_json()}

    @api.get("/api/projects/{pid}/segments/{seg_id}/attempts/{lang}")
    def list_attempts(pid: str, seg_id: str, lang: str) -> dict:
        """Legacy: returns r0 attempts. Use the rotation-aware endpoint
        below for non-canonical rotations."""
        if lang not in LANGUAGES:
            raise HTTPException(400, f"unknown lang: {lang}")
        return {"attempts": store.list_attempts(pid, seg_id, lang, CANONICAL_ROTATION)}

    @api.get("/api/projects/{pid}/segments/{seg_id}/attempts/{lang}/{rotation_id}")
    def list_attempts_rotation(
        pid: str, seg_id: str, lang: str, rotation_id: str,
    ) -> dict:
        if lang not in LANGUAGES:
            raise HTTPException(400, f"unknown lang: {lang}")
        if not rotation_id.startswith("r"):
            raise HTTPException(400, f"invalid rotation_id: {rotation_id}")
        return {"attempts": store.list_attempts(pid, seg_id, lang, rotation_id)}

    @api.get("/api/projects/{pid}/audio/{seg_id}/{lang}/{name}")
    def get_audio(pid: str, seg_id: str, lang: str, name: str):
        """Legacy audio path: returns the r0 file (with on-disk back-compat
        fallback to pre-rotation layouts)."""
        if lang not in LANGUAGES:
            raise HTTPException(400, f"unknown lang: {lang}")
        if "/" in name or ".." in name or not name.endswith(".mp3"):
            raise HTTPException(400, "invalid name")
        att_id = name[:-4]
        path = store.attempt_mp3(pid, seg_id, lang, att_id, CANONICAL_ROTATION)
        if not path.exists():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type="audio/mpeg", filename=name)

    @api.get("/api/projects/{pid}/audio/{seg_id}/{lang}/{rotation_id}/{name}")
    def get_audio_rotation(
        pid: str, seg_id: str, lang: str, rotation_id: str, name: str,
    ):
        if lang not in LANGUAGES:
            raise HTTPException(400, f"unknown lang: {lang}")
        if not rotation_id.startswith("r"):
            raise HTTPException(400, f"invalid rotation_id: {rotation_id}")
        if "/" in name or ".." in name or not name.endswith(".mp3"):
            raise HTTPException(400, "invalid name")
        att_id = name[:-4]
        path = store.attempt_mp3(pid, seg_id, lang, att_id, rotation_id)
        if not path.exists():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type="audio/mpeg", filename=name)

    @api.get("/api/projects/{pid}/zip")
    def project_zip(pid: str):
        try:
            proj = store.load(pid)
        except FileNotFoundError:
            raise HTTPException(404, "project not found")

        buf = io.BytesIO()
        rotation_ids = proj.rotation_ids()
        multi_rotation = len(rotation_ids) > 1
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "project.json",
                Path(store.project_json(pid)).read_text(encoding="utf-8"),
            )
            for rotation_id in rotation_ids:
                # Per-rotation, options follow this rotation's order so
                # option_1.mp3 is always "the option you press 1 for".
                if rotation_id == CANONICAL_ROTATION:
                    option_order = [s.id for s in proj.segments if s.type == "option"]
                else:
                    try:
                        idx = int(rotation_id[1:])
                        option_order = list(proj.rotations[idx])
                    except (ValueError, IndexError):
                        option_order = [s.id for s in proj.segments if s.type == "option"]
                option_position = {sid: i + 1 for i, sid in enumerate(option_order)}

                for seg in proj.segments:
                    # For non-options, only r0 matters — they're rotation-invariant.
                    effective_rid = (
                        rotation_id if seg.type == "option" else CANONICAL_ROTATION
                    )
                    per = seg.current_takes.get("") or {}
                    for lang in (proj.langs or []):
                        per_lang = seg.current_takes.get(lang) or {}
                        att_id = per_lang.get(effective_rid)
                        if not att_id:
                            continue
                        src = store.attempt_mp3(pid, seg.id, lang, att_id, effective_rid)
                        if not src.exists():
                            continue
                        if seg.type == "question":
                            label = "question"
                        elif seg.type == "option":
                            label = f"option_{option_position.get(seg.id, 0)}"
                        else:
                            label = f"body_{proj.option_index(seg.id) or 1}"
                        prefix = f"{rotation_id}/" if multi_rotation else ""
                        zf.write(src, f"{prefix}{lang}/{label}.mp3")
        buf.seek(0)
        headers = {"Content-Disposition": f'attachment; filename="{proj.id}.zip"'}
        return StreamingResponse(buf, media_type="application/zip", headers=headers)

    @api.post("/api/projects/{pid}/open-folder")
    def open_project_folder(pid: str) -> dict:
        d = store.project_dir(pid)
        if not d.exists():
            raise HTTPException(404, "project folder missing")
        if sys.platform == "darwin":
            subprocess.run(["open", str(d)], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(d)], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(d)], check=False)
        else:
            raise HTTPException(501, "unsupported platform")
        return {"opened": str(d)}

    # ------------------------------------------------------------------
    # Job polling (shared by regen + legacy generate)
    # ------------------------------------------------------------------

    def _job_or_404(job_id: str) -> JobProgress:
        with jobs_lock:
            p = jobs.get(job_id)
        if not p:
            raise HTTPException(404, f"unknown job: {job_id}")
        return p

    def _job_payload(job_id: str, p: JobProgress) -> dict[str, Any]:
        return {
            "id": job_id,
            "status": p.status,
            "error": p.error,
            "run_id": p.run_id,
            "elapsed_s": round(p.elapsed_s, 2),
            "by_lang": {code: asdict(lp) for code, lp in p.by_lang.items()},
            "translations": p.translations,
            "project_id": p.project_id,
            "segment_id": p.segment_id,
            "new_attempts": p.new_attempts,
        }

    @api.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return _job_payload(job_id, _job_or_404(job_id))

    @api.get("/api/jobs")
    def list_active_jobs() -> list[dict[str, Any]]:
        """Active (queued + running) jobs in submission order."""
        out: list[dict[str, Any]] = []
        with jobs_lock:
            for jid in list(job_order):
                p = jobs.get(jid)
                if p is None:
                    continue
                if p.status in ("queued", "running"):
                    out.append(_job_payload(jid, p))
                else:
                    # Drop completed jobs from the active list (kept in `jobs`
                    # so per-job GETs still work for a moment afterwards).
                    job_order.remove(jid)
        return out

    # ------------------------------------------------------------------
    # Legacy one-shot generate (kept for the CLI / scripts)
    # ------------------------------------------------------------------

    @api.post("/api/generate")
    def generate(req: GenerateRequest) -> dict[str, str]:
        if not req.question.strip():
            raise HTTPException(400, "question must be non-empty")
        if not req.options:
            raise HTTPException(400, "at least one option required")
        try:
            codes = parse_langs(",".join(req.langs)) if req.langs else list(ALL_LANG_CODES)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        for code in req.voices:
            if code not in LANGUAGES:
                raise HTTPException(400, f"unknown language in voices: {code}")
        if req.pace not in PACE_PHRASES:
            raise HTTPException(400, f"unknown pace: {req.pace}")
        for code, p in req.paces.items():
            if code not in LANGUAGES or p not in PACE_PHRASES:
                raise HTTPException(400, f"invalid pace for {code}: {p}")

        job_id = uuid.uuid4().hex[:12]
        progress = JobProgress()

        def worker():
            try:
                run_pipeline(
                    req.question, req.options, codes,
                    out_root=out_root, voices=req.voices,
                    paces=req.paces, default_pace=req.pace,
                    progress=progress,
                )
            except Exception as exc:
                log.exception("job %s failed: %s", job_id, exc)

        _register_job(job_id, progress)
        # Legacy one-shot pipeline does its own translate+synth internally;
        # park it on the audio queue so it serializes against regen audio
        # tasks (the model can only run one inference at a time anyway).
        audio_queue.put(worker)
        return {"job_id": job_id}

    @api.get("/api/jobs/{job_id}/audio/{lang}/{name}")
    def get_legacy_audio(job_id: str, lang: str, name: str):
        # Legacy path used only by the old one-shot UI (no longer mounted).
        p = _job_or_404(job_id)
        if not p.run_id or "/" in name or ".." in name or not name.endswith(".mp3"):
            raise HTTPException(400, "invalid name")
        path = out_root / p.run_id / lang / name
        if not path.exists():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type="audio/mpeg", filename=name)

    # ------------------------------------------------------------------
    # Static
    # ------------------------------------------------------------------

    @api.get("/")
    def index():
        cfg = app_config.load()
        page = "onboarding.html" if not cfg.onboarded else "index.html"
        return FileResponse(STATIC_DIR / page, media_type="text/html")

    @api.get("/onboarding")
    def onboarding_page():
        return FileResponse(STATIC_DIR / "onboarding.html", media_type="text/html")

    @api.get("/docs")
    def docs_page():
        return FileResponse(STATIC_DIR / "docs.html", media_type="text/html")

    @api.get("/api/docs")
    def list_docs() -> list[dict]:
        """Curated index of in-app docs."""
        repo_root = DOCS_DIR.parent
        out: list[dict] = []
        for entry in DOC_INDEX:
            if (DOCS_DIR / entry["file"]).exists():
                out.append({
                    "id": entry["file"],
                    "title": entry["title"],
                    "summary": entry["summary"],
                    "scope": "docs",
                })
        for rel, meta in EXTRA_DOC_FILES.items():
            if (repo_root / rel).exists():
                out.append({
                    "id": rel,
                    "title": meta["title"],
                    "summary": meta["summary"],
                    "scope": "repo",
                })
        return out

    @api.get("/api/docs/{rel:path}")
    def get_doc(rel: str):
        """Return the markdown source of a doc, plain-text. The path is
        resolved relative to the repo root and confined to known files
        below — we don't accept arbitrary paths."""
        repo_root = DOCS_DIR.parent
        candidates = {
            entry["file"]: DOCS_DIR / entry["file"] for entry in DOC_INDEX
        }
        for repo_rel in EXTRA_DOC_FILES:
            candidates[repo_rel] = repo_root / repo_rel
        target = candidates.get(rel)
        if target is None or not target.exists():
            raise HTTPException(404, "doc not found")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(target.read_text(encoding="utf-8"),
                                 media_type="text/markdown; charset=utf-8")

    if STATIC_DIR.exists():
        api.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return api


# ---------------------------------------------------------------------------


def _project_payload(pid: str, store: ProjectStore) -> dict:
    try:
        proj = store.load(pid)
    except FileNotFoundError:
        raise HTTPException(404, "project not found")
    return proj.to_json()


def _option_index(proj, seg_id: str) -> int:
    """Position of the segment among options (1-based) for naming files."""
    n = 0
    for s in proj.segments:
        if s.type == "option":
            n += 1
            if s.id == seg_id:
                return n
    return 0
