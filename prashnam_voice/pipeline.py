from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import soundfile as sf

from . import __version__
from . import engines
from .audio import wav_to_mp3
from .cache import cache_path, link_or_copy
from .config import (
    ALL_LANG_CODES,
    DEFAULT_PACE,
    LANGUAGES,
    PACE_PHRASES,
    TRANSLATION_MODEL,
    TTS_MODEL,
)
from .projects import (
    CANONICAL_ROTATION,
    Project,
    ProjectStore,
    Segment,
    effective_text,
)

log = logging.getLogger(__name__)


def make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _item_filename(idx: int) -> str:
    return "question.mp3" if idx == 0 else f"option_{idx}.mp3"


# ---------------------------------------------------------------------------
# Granular, project-aware operations
# ---------------------------------------------------------------------------


def translate_segments(
    project: Project,
    segments: Iterable[Segment],
    langs: list[str],
    rotation_id: str = CANONICAL_ROTATION,
) -> dict[str, dict[str, str]]:
    """Translate the given segments into the given languages, for one rotation.

    Translation source is `effective_text(project, segment, lang, rotation_id)`
    — body wrapped by the project template (with `{n}` taking the option's
    position in this rotation), then numeral-normalized. Mutates each
    segment's `translations[lang][rotation_id]` slot in place.
    """
    seg_list = [s for s in segments if s.english.strip()]
    if not seg_list or not langs:
        return {}

    translator, cfg = engines.get_translator()
    new: dict[str, dict[str, str]] = {s.id: {} for s in seg_list}
    for lang in langs:
        if lang not in LANGUAGES:
            raise ValueError(f"unsupported lang: {lang}")
        texts = [
            effective_text(project, s, lang=lang, rotation_id=rotation_id)
            for s in seg_list
        ]
        translated = translator.translate_batch(texts, lang, cfg)
        for seg, t in zip(seg_list, translated):
            seg.set_translation(lang, rotation_id, t)
            new[seg.id][lang] = t
    return new


def synthesize_segment_lang(
    store: ProjectStore,
    project: Project,
    segment: Segment,
    lang: str,
    rotation_id: str = CANONICAL_ROTATION,
) -> str:
    """Generate one MP3 for (segment, lang, rotation_id). Returns the new
    attempt id. Goes through the active TTS adapter; cache key includes
    the adapter name + the rotation-specific text.
    """
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported lang: {lang}")
    text = (segment.translation_for(lang, rotation_id) or "").strip()
    if not text:
        raise ValueError(
            f"segment {segment.id} has no {lang}/{rotation_id} translation; translate first"
        )

    voice = project.voice_for(lang, segment)
    pace = project.pace_for(lang, segment)

    tts, cfg = engines.get_tts()
    cached = cache_path(text, lang, voice, pace, model_id=tts.name)
    if not cached.exists():
        mp3_bytes = tts.synthesize(text, lang, voice, pace, cfg)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(mp3_bytes)
    duration = _read_mp3_duration(cached)

    return store.write_attempt(
        project.id,
        segment.id,
        lang,
        voice=voice,
        pace=pace,
        source_text=text,
        duration_s=duration,
        model_id=tts.name,
        mp3_src=cached,
        rotation_id=rotation_id,
    )


def _read_mp3_duration(path: Path) -> float:
    # Cheap fallback: re-read with soundfile when possible.
    try:
        info = sf.info(str(path))
        return info.frames / float(info.samplerate)
    except Exception:  # noqa: BLE001
        return 0.0


def _read_wav_duration(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / float(info.samplerate)


# ---------------------------------------------------------------------------
# Job runner shared by CLI and HTTP layer
# ---------------------------------------------------------------------------


@dataclass
class LangProgress:
    translated: bool = False
    audio_started: bool = False     # synthesis kicked off for this lang
    audio_done: int = 0
    audio_total: int = 0
    cache_hits: int = 0


@dataclass
class JobProgress:
    status: str = "queued"        # queued | running | done | error
    error: str | None = None
    run_id: str = ""
    out_dir: str = ""
    by_lang: dict[str, "LangProgress"] = field(default_factory=dict)
    translations: dict[str, list[str]] | None = None
    elapsed_s: float = 0.0
    # Optional, for project regenerations:
    project_id: str | None = None
    segment_id: str | None = None
    new_attempts: dict[str, str] = field(default_factory=dict)  # lang -> att_id


ProgressCallback = Callable[[JobProgress], None]


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Backwards-compatible one-shot pipeline (used by `prashnam-voice generate`)
# ---------------------------------------------------------------------------


def run_pipeline(
    question: str,
    options: list[str],
    langs: list[str],
    out_root: Path,
    voices: dict[str, str] | None = None,
    paces: dict[str, str] | None = None,
    default_pace: str = DEFAULT_PACE,
    progress: JobProgress | None = None,
    on_update: ProgressCallback | None = None,
) -> JobProgress:
    """One-shot translate+synthesize for a single poll. Writes everything
    under out_root/<run_id>/. Used by the legacy `generate` CLI command.
    """
    if not question.strip():
        raise ValueError("question must be non-empty")
    if not options:
        raise ValueError("at least one option is required")
    for code in langs:
        if code not in LANGUAGES:
            raise ValueError(f"Unsupported language: {code}")
    if default_pace not in PACE_PHRASES:
        raise ValueError(f"Unknown pace: {default_pace}")
    paces = paces or {}
    for code, p in paces.items():
        if p not in PACE_PHRASES:
            raise ValueError(f"Unknown pace {p!r} for {code}")

    voices = voices or {}
    items = [question, *options]
    n_items = len(items)
    run_id = make_run_id()
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    p = progress or JobProgress()
    p.status = "running"
    p.run_id = run_id
    p.out_dir = str(run_dir)
    p.by_lang = {code: LangProgress(audio_total=n_items) for code in langs}
    if on_update:
        on_update(p)

    t0 = time.time()
    try:
        translator, tcfg = engines.get_translator()
        translations: dict[str, list[str]] = {}
        for code in langs:
            translations[code] = translator.translate_batch(items, code, tcfg)
            p.by_lang[code].translated = True
            if on_update:
                on_update(p)

        p.translations = translations
        _save_json(
            run_dir / "translations.json",
            {
                "question_en": question,
                "options_en": options,
                "by_lang": {
                    code: {
                        "question": translations[code][0],
                        "options": translations[code][1:],
                    }
                    for code in langs
                },
            },
        )
        if on_update:
            on_update(p)

        tts, tts_cfg = engines.get_tts()
        for code in langs:
            voice = voices.get(code) or LANGUAGES[code].voice
            pace = paces.get(code, default_pace)
            lang_dir = run_dir / code
            lang_dir.mkdir(parents=True, exist_ok=True)
            p.by_lang[code].audio_started = True
            if on_update:
                on_update(p)
            for idx, text in enumerate(translations[code]):
                cached = cache_path(text, code, voice, pace, model_id=tts.name)
                target = lang_dir / _item_filename(idx)
                if cached.exists():
                    link_or_copy(cached, target)
                    p.by_lang[code].cache_hits += 1
                else:
                    mp3_bytes = tts.synthesize(text, code, voice, pace, tts_cfg)
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(mp3_bytes)
                    link_or_copy(cached, target)
                p.by_lang[code].audio_done += 1
                if on_update:
                    on_update(p)

        p.elapsed_s = time.time() - t0
        _save_json(
            run_dir / "meta.json",
            {
                "run_id": run_id,
                "version": __version__,
                "translation_model": TRANSLATION_MODEL,
                "tts_model": TTS_MODEL,
                "question_en": question,
                "options_en": options,
                "langs": langs,
                "voices": {code: voices.get(code) or LANGUAGES[code].voice for code in langs},
                "paces": {code: paces.get(code, default_pace) for code in langs},
                "elapsed_s": round(p.elapsed_s, 2),
                "by_lang": {code: asdict(p.by_lang[code]) for code in langs},
            },
        )
        p.status = "done"
        if on_update:
            on_update(p)
        return p
    except Exception as exc:
        p.status = "error"
        p.error = f"{type(exc).__name__}: {exc}"
        p.elapsed_s = time.time() - t0
        if on_update:
            on_update(p)
        raise


# ---------------------------------------------------------------------------
# Project regeneration job — used by the web app for a single (segment, langs)
# ---------------------------------------------------------------------------


def regenerate_segment(
    store: ProjectStore,
    project_id: str,
    segment_id: str,
    langs: list[str],
    *,
    auto_translate: bool = True,
    set_current: bool = True,
    progress: JobProgress | None = None,
    on_update: ProgressCallback | None = None,
) -> JobProgress:
    """Regenerate audio for one segment in the given languages.

    If `auto_translate=True`, missing translations are filled first using the
    project's current English text.
    If `set_current=True`, the new attempt becomes the current take.
    """
    p = progress or JobProgress()
    p.status = "running"
    p.project_id = project_id
    p.segment_id = segment_id
    p.by_lang = {c: LangProgress(audio_total=1) for c in langs}
    if on_update:
        on_update(p)

    t0 = time.time()
    try:
        proj = store.load(project_id)
        seg = proj.find_segment(segment_id)
        if not seg.english.strip():
            raise ValueError("segment has empty English text")

        # Translate any langs missing for this segment.
        if auto_translate:
            missing = [c for c in langs if not seg.translations.get(c)]
            if missing:
                translate_segments(proj, [seg], missing)
                store.update(proj)
            for c in langs:
                p.by_lang[c].translated = True
                if on_update:
                    on_update(p)
        else:
            for c in langs:
                p.by_lang[c].translated = bool(seg.translations.get(c))

        # Synthesize per language — strictly sequential. Mark `audio_started`
        # before the call so the UI can distinguish "currently synthesizing"
        # from "queued, waiting".
        for c in langs:
            p.by_lang[c].audio_started = True
            if on_update:
                on_update(p)
            try:
                att_id = synthesize_segment_lang(store, proj, seg, c)
                p.new_attempts[c] = att_id
                if set_current:
                    store.set_current_take(project_id, segment_id, c, att_id)
                p.by_lang[c].audio_done = 1
            except Exception as exc:  # noqa: BLE001
                log.exception("synth failed for %s/%s/%s: %s", project_id, segment_id, c, exc)
                p.error = f"{c}: {type(exc).__name__}: {exc}"
            if on_update:
                on_update(p)

        p.elapsed_s = time.time() - t0
        # If at least one lang succeeded, we're done; otherwise mark error.
        if any(lp.audio_done for lp in p.by_lang.values()):
            p.status = "done"
        else:
            p.status = "error"
            if not p.error:
                p.error = "no audio produced"
        if on_update:
            on_update(p)
        return p
    except Exception as exc:
        p.status = "error"
        p.error = f"{type(exc).__name__}: {exc}"
        p.elapsed_s = time.time() - t0
        if on_update:
            on_update(p)
        raise


def expand_langs(spec: list[str] | None) -> list[str]:
    if not spec:
        return list(ALL_LANG_CODES)
    return spec
