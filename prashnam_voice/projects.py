"""Project model + persistence.

A project is a directory under <projects_root>/<id>/ containing:

  project.json
  audio/<segment_id>/<lang>/<attempt_id>.mp3
  audio/<segment_id>/<lang>/<attempt_id>.json   # voice, pace, source_text, etc.

The on-disk JSON is the source of truth. The web UI mutates state by sending
PATCH/POST requests; everything else (CLI, scripts) reads/writes the same files.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .config import (
    ALL_LANG_CODES,
    DEFAULT_PACE,
    LANGUAGES,
    PACE_PHRASES,
)

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# IDs and time
# ----------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s[:40] or "project").rstrip("-")


# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------

SEGMENT_TYPES = ("question", "option", "body")
DEFAULT_DOMAIN = "poll"

# Defaults for new projects. The prashnam.ai team can override per-project
# from settings; existing projects without these fields stay un-templated.
DEFAULT_QUESTION_TEMPLATE = (
    "Namaskar, this is a call from Prashnam, an independent polling agency. "
    "{body}"
)
DEFAULT_OPTION_TEMPLATE = "If you think {body}, then press {n}."


CANONICAL_ROTATION = "r0"


def _migrate_per_rotation(
    raw: dict | None,
) -> dict[str, dict[str, str]]:
    """Convert legacy `{lang: str}` translations / current_takes into the
    rotation-aware `{lang: {rotation_id: str}}` shape. New projects already
    write the new shape, so this is a one-shot migration on load.
    """
    out: dict[str, dict[str, str]] = {}
    for lang, val in (raw or {}).items():
        if isinstance(val, str):
            out[lang] = {CANONICAL_ROTATION: val} if val else {}
        elif isinstance(val, dict):
            out[lang] = {k: v for k, v in val.items() if isinstance(v, str)}
    return out


@dataclass
class Segment:
    id: str
    type: str                                  # "question" | "option" | "body"
    english: str = ""
    use_template: bool = True                  # opt out of project's wrapping
    lock_at_end: bool = False                  # NOTA / "Don't know" — never shuffles
    # Per-language, per-rotation translation. The "r0" rotation is always
    # the canonical (declared) order.
    translations: dict[str, dict[str, str]] = field(default_factory=dict)
    # Per-language, per-rotation current take pointer.
    current_takes: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Segment":
        return cls(
            id=d["id"],
            type=d["type"],
            english=d.get("english", ""),
            use_template=bool(d.get("use_template", True)),
            lock_at_end=bool(d.get("lock_at_end", False)),
            translations=_migrate_per_rotation(d.get("translations")),
            current_takes=_migrate_per_rotation(d.get("current_takes")),
        )

    # ----- per-rotation accessors -----

    def take_for(self, lang: str, rotation_id: str = CANONICAL_ROTATION) -> str | None:
        per = self.current_takes.get(lang) or {}
        return per.get(rotation_id)

    def translation_for(self, lang: str, rotation_id: str = CANONICAL_ROTATION) -> str | None:
        per = self.translations.get(lang) or {}
        return per.get(rotation_id)

    def set_take(self, lang: str, rotation_id: str, attempt_id: str) -> None:
        self.current_takes.setdefault(lang, {})[rotation_id] = attempt_id

    def set_translation(self, lang: str, rotation_id: str, text: str) -> None:
        self.translations.setdefault(lang, {})[rotation_id] = text


@dataclass
class Project:
    id: str
    name: str
    created_at: str
    updated_at: str
    domain: str = DEFAULT_DOMAIN
    langs: list[str] = field(default_factory=list)
    default_pace: str = DEFAULT_PACE
    voices: dict[str, str] = field(default_factory=dict)
    paces: dict[str, str] = field(default_factory=dict)
    question_template: str = DEFAULT_QUESTION_TEMPLATE
    option_template: str = DEFAULT_OPTION_TEMPLATE
    body_template: str = ""           # used by announcement domain
    # Rotations — when `rotation_count` > 1, each regen produces one set of
    # audio per rotation. "r0" is always the canonical (declared) order;
    # additional rotations shuffle the non-locked options. Locked options
    # (Segment.lock_at_end=True) stay last in every rotation.
    rotation_count: int = 1
    rotation_seed: int | None = None
    # Persisted shuffled orderings of option-segment IDs. `rotations[i]`
    # is the order for rotation_id `r{i}`. Empty list → only `r0` exists
    # (rotation_count == 1).
    rotations: list[list[str]] = field(default_factory=list)
    # Pronunciation overrides applied to every effective text before
    # translation. Keys are case-sensitive substrings (typically proper
    # nouns: BJP, AAP, names). The "global" entry applies to every
    # language; per-lang entries (keyed by our lang code) override.
    #
    #   {"global": {"BJP": "bee jay pee"},
    #    "hi":     {"BJP": "बीजेपी"}}
    #
    # Per-language overrides only kick in when generating audio for that
    # language. Global is always applied. Substitution is whole-token
    # only — `\bKEY\b` — so BJP doesn't smash into "objpop".
    lexicon: dict[str, dict[str, str]] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["segments"] = [s.to_json() for s in self.segments]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Project":
        return cls(
            id=d["id"],
            name=d["name"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            domain=d.get("domain", DEFAULT_DOMAIN),
            langs=list(d.get("langs") or []),
            default_pace=d.get("default_pace", DEFAULT_PACE),
            voices=dict(d.get("voices") or {}),
            paces=dict(d.get("paces") or {}),
            question_template=d.get("question_template", DEFAULT_QUESTION_TEMPLATE),
            option_template=d.get("option_template", DEFAULT_OPTION_TEMPLATE),
            body_template=d.get("body_template", ""),
            rotation_count=int(d.get("rotation_count", 1) or 1),
            rotation_seed=d.get("rotation_seed"),
            rotations=[list(r) for r in (d.get("rotations") or [])],
            lexicon={
                scope: dict(entries or {})
                for scope, entries in (d.get("lexicon") or {}).items()
            },
            segments=[Segment.from_json(s) for s in (d.get("segments") or [])],
        )

    def find_segment(self, seg_id: str) -> Segment:
        for s in self.segments:
            if s.id == seg_id:
                return s
        raise KeyError(seg_id)

    def voice_for(self, lang: str) -> str:
        return self.voices.get(lang) or LANGUAGES[lang].voice

    def pace_for(self, lang: str) -> str:
        return self.paces.get(lang, self.default_pace)

    def option_index(self, seg_id: str) -> int:
        """1-based position among option segments in the canonical (declared)
        order. Returns 0 for non-options."""
        n = 0
        for s in self.segments:
            if s.type == "option":
                n += 1
                if s.id == seg_id:
                    return n
        return 0

    def rotation_ids(self) -> list[str]:
        """All active rotation ids. Always includes 'r0'; with rotation_count>1
        also r1..r{count-1}, taken from `rotations` if computed."""
        if self.rotation_count <= 1:
            return [CANONICAL_ROTATION]
        return [f"r{i}" for i in range(self.rotation_count)]

    def has_rotations(self) -> bool:
        return self.rotation_count > 1

    def option_position_in_rotation(self, seg_id: str, rotation_id: str) -> int:
        """1-based position of `seg_id` in the given rotation, or in the
        canonical order when no rotation is set / found."""
        if rotation_id == CANONICAL_ROTATION or not self.rotations:
            return self.option_index(seg_id)
        try:
            idx = int(rotation_id[1:])
        except (ValueError, IndexError):
            return self.option_index(seg_id)
        if idx < 0 or idx >= len(self.rotations):
            return self.option_index(seg_id)
        rotation = self.rotations[idx]
        try:
            return rotation.index(seg_id) + 1
        except ValueError:
            return self.option_index(seg_id)


def compute_rotations(
    options_in_order: list[Segment],
    count: int,
    seed: int | None = None,
) -> list[list[str]]:
    """Produce `count` distinct orderings of option segment ids.

    rotations[0] is always the canonical declared order. Locked options
    (`lock_at_end=True`) appear last in every rotation, in declared
    order among themselves. The remaining options are shuffled with a
    deterministic RNG seeded from `seed` (None = random).

    Falls back to fewer rotations than requested if the unlocked-option
    permutation space is too small to give `count` distinct orderings.
    """
    import random as _rnd

    if count <= 1:
        return [[s.id for s in options_in_order]]

    free = [s.id for s in options_in_order if not s.lock_at_end]
    locked = [s.id for s in options_in_order if s.lock_at_end]

    rng = _rnd.Random(seed)
    canonical = [s.id for s in options_in_order]
    out: list[list[str]] = [canonical]
    seen: set[tuple[str, ...]] = {tuple(canonical)}

    # Cap attempts so we don't loop forever for tiny option sets.
    import math
    max_distinct = math.factorial(len(free)) if free else 1
    target = min(count, max_distinct)
    attempts = 0
    while len(out) < target and attempts < target * 20:
        shuffled = list(free)
        rng.shuffle(shuffled)
        ordering = shuffled + locked
        key = tuple(ordering)
        if key not in seen:
            seen.add(key)
            out.append(ordering)
        attempts += 1
    return out


def effective_text(
    project: Project,
    segment: Segment,
    lang: str | None = None,
    rotation_id: str = CANONICAL_ROTATION,
) -> str:
    """The text that will actually be translated and spoken, after:
      1. applying any pronunciation lexicon entries (global + per-lang),
      2. wrapping with the project template (if enabled for this segment),
      3. normalizing numerals to English words.

    Empty bodies stay empty so the pipeline knows to skip.

    `lang` is optional — pass it when computing for a specific target
    language so the per-language lexicon can override the global one.
    Without `lang` only the global lexicon applies (used for translation
    where one input becomes N outputs).
    """
    from .text_normalize import numerals_to_words

    body = segment.english.strip()
    if not body:
        return ""

    # 1. Lexicon — apply substitutions first so the template + numeral
    #    pass operate on the corrected text.
    body = _apply_lexicon(body, project.lexicon, lang)

    # 2. Template wrapping
    raw = body
    if segment.use_template:
        if segment.type == "question":
            tmpl = (project.question_template or "").strip()
            if tmpl:
                try:
                    raw = tmpl.format(body=body)
                except (KeyError, IndexError):
                    raw = body
        elif segment.type == "option":
            tmpl = (project.option_template or "").strip()
            if tmpl:
                # Per-rotation position so {n} reflects the option's place
                # in the chosen rotation, not the declared order.
                n = project.option_position_in_rotation(segment.id, rotation_id)
                try:
                    raw = tmpl.format(body=body, n=n)
                except (KeyError, IndexError):
                    raw = body
        elif segment.type == "body":
            tmpl = (project.body_template or "").strip()
            if tmpl:
                try:
                    raw = tmpl.format(body=body)
                except (KeyError, IndexError):
                    raw = body

    # 3. Numeral normalization
    return numerals_to_words(raw)


def _prune_stale_rotation_state(project: "Project") -> None:
    """Drop translations and current_takes for rotation ids that no longer
    exist after a rotation change. Always preserves r0 (canonical).

    Called after enable/disable/reshuffle/lock-toggle changes the active
    rotation set so the project's persisted state stays in sync.
    """
    valid = set(project.rotation_ids())
    for seg in project.segments:
        # Question segments don't rotate — pin their state to r0 only.
        if seg.type != "option":
            for lang, per in list(seg.translations.items()):
                if per:
                    canonical = per.get(CANONICAL_ROTATION) or next(iter(per.values()), "")
                    seg.translations[lang] = {CANONICAL_ROTATION: canonical} if canonical else {}
            for lang, per in list(seg.current_takes.items()):
                if per:
                    canonical = per.get(CANONICAL_ROTATION) or next(iter(per.values()), "")
                    seg.current_takes[lang] = {CANONICAL_ROTATION: canonical} if canonical else {}
            continue
        # Options: keep only the rotation ids that are still valid.
        for lang, per in list(seg.translations.items()):
            seg.translations[lang] = {r: t for r, t in per.items() if r in valid}
        for lang, per in list(seg.current_takes.items()):
            seg.current_takes[lang] = {r: aid for r, aid in per.items() if r in valid}


def _apply_lexicon(
    text: str, lexicon: dict[str, dict[str, str]], lang: str | None
) -> str:
    """Whole-word substitution with per-language overrides over global."""
    import re

    if not lexicon:
        return text
    merged: dict[str, str] = {}
    merged.update(lexicon.get("global", {}) or {})
    if lang:
        merged.update(lexicon.get(lang, {}) or {})
    if not merged:
        return text
    # Process longest keys first so longer patterns win over substrings.
    for key in sorted(merged, key=len, reverse=True):
        value = merged[key]
        if not key:
            continue
        # \b is fine for ASCII keys; for non-ASCII we just match the literal
        # token surrounded by non-word boundaries via lookahead/behind.
        pattern = re.compile(rf"(?<!\w){re.escape(key)}(?!\w)")
        text = pattern.sub(value, text)
    return text


# ----------------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------------


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _project_lock(pid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(pid)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[pid] = lock
        return lock


class ProjectStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ----- paths -----

    def project_dir(self, pid: str) -> Path:
        return self.root / pid

    def project_json(self, pid: str) -> Path:
        return self.project_dir(pid) / "project.json"

    def audio_dir(
        self, pid: str, seg_id: str, lang: str, rotation_id: str = CANONICAL_ROTATION
    ) -> Path:
        return self.project_dir(pid) / "audio" / seg_id / lang / rotation_id

    def _legacy_audio_dir(self, pid: str, seg_id: str, lang: str) -> Path:
        """Pre-rotation path: audio/<seg>/<lang>/<att>.mp3 directly. Used as
        a read-only fallback for projects created before rotations existed."""
        return self.project_dir(pid) / "audio" / seg_id / lang

    def attempt_mp3(
        self, pid: str, seg_id: str, lang: str, att_id: str,
        rotation_id: str = CANONICAL_ROTATION,
    ) -> Path:
        new = self.audio_dir(pid, seg_id, lang, rotation_id) / f"{att_id}.mp3"
        if new.exists():
            return new
        if rotation_id == CANONICAL_ROTATION:
            old = self._legacy_audio_dir(pid, seg_id, lang) / f"{att_id}.mp3"
            if old.exists():
                return old
        return new

    def attempt_meta(
        self, pid: str, seg_id: str, lang: str, att_id: str,
        rotation_id: str = CANONICAL_ROTATION,
    ) -> Path:
        new = self.audio_dir(pid, seg_id, lang, rotation_id) / f"{att_id}.json"
        if new.exists():
            return new
        if rotation_id == CANONICAL_ROTATION:
            old = self._legacy_audio_dir(pid, seg_id, lang) / f"{att_id}.json"
            if old.exists():
                return old
        return new

    # ----- listing -----

    def list_projects(self) -> list[dict]:
        out: list[dict] = []
        for d in sorted(self.root.iterdir(), key=lambda p: p.name):
            if not d.is_dir():
                continue
            pj = d / "project.json"
            if not pj.exists():
                continue
            try:
                payload = json.loads(pj.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping %s: %s", pj, exc)
                continue
            out.append({
                "id": payload.get("id", d.name),
                "name": payload.get("name", d.name),
                "created_at": payload.get("created_at", ""),
                "updated_at": payload.get("updated_at", ""),
                "segment_count": len(payload.get("segments") or []),
                "langs": payload.get("langs") or [],
            })
        out.sort(key=lambda x: x["updated_at"], reverse=True)
        return out

    # ----- crud -----

    def create(
        self,
        name: str,
        langs: Iterable[str] | None = None,
        domain: str = DEFAULT_DOMAIN,
    ) -> Project:
        from . import domains as domains_mod

        name = (name or "").strip() or "Untitled"
        codes = [c for c in (langs or ALL_LANG_CODES) if c in LANGUAGES]
        slug = _slugify(name)
        pid = f"{slug}-{uuid.uuid4().hex[:6]}"
        now = _now()

        # Seed the templates from the chosen domain so a fresh
        # `announcement` project doesn't carry the polling preamble.
        try:
            pack = domains_mod.get(domain)
        except KeyError:
            raise ValueError(f"unknown domain: {domain}")
        defaults = pack.default_templates

        proj = Project(
            id=pid, name=name, created_at=now, updated_at=now,
            domain=domain,
            langs=codes or list(ALL_LANG_CODES),
            question_template=defaults.get("question_template", DEFAULT_QUESTION_TEMPLATE),
            option_template=defaults.get("option_template", DEFAULT_OPTION_TEMPLATE),
            body_template=defaults.get("body_template", ""),
        )
        with _project_lock(pid):
            self.project_dir(pid).mkdir(parents=True, exist_ok=False)
            self._write(proj)
        log.info("created project %s (domain=%s, name=%s)", pid, domain, name)
        return proj

    def load(self, pid: str) -> Project:
        path = self.project_json(pid)
        if not path.exists():
            raise FileNotFoundError(pid)
        return Project.from_json(json.loads(path.read_text(encoding="utf-8")))

    def _write(self, proj: Project) -> None:
        proj.updated_at = _now()
        path = self.project_json(proj.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(proj.to_json(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)

    def update(self, proj: Project) -> Project:
        with _project_lock(proj.id):
            self._write(proj)
        return proj

    def delete(self, pid: str) -> None:
        d = self.project_dir(pid)
        if d.exists():
            shutil.rmtree(d)

    def mutate(self, pid: str, fn) -> Project:
        """Load → mutate → save under the per-project lock."""
        with _project_lock(pid):
            proj = self.load(pid)
            fn(proj)
            self._write(proj)
            return proj

    # ----- segments -----

    def add_segment(self, pid: str, seg_type: str, english: str = "") -> Segment:
        if seg_type not in SEGMENT_TYPES:
            raise ValueError(f"invalid segment type: {seg_type}")
        seg = Segment(id=_short_id("seg"), type=seg_type, english=english)
        self.mutate(pid, lambda p: p.segments.append(seg))
        return seg

    def edit_segment_english(
        self, pid: str, seg_id: str, english: str
    ) -> tuple[Segment, list[str]]:
        """Edit english text. Returns (segment, langs_invalidated).

        Translations + current_takes for that segment are cleared because the
        source text has changed. The old attempt files stay on disk (history).
        """
        invalidated: list[str] = []

        def _do(p: Project):
            seg = p.find_segment(seg_id)
            if english.strip() == seg.english.strip():
                return
            seg.english = english
            invalidated.extend(seg.translations.keys())
            seg.translations = {}
            seg.current_takes = {}

        self.mutate(pid, _do)
        return self.load(pid).find_segment(seg_id), invalidated

    def delete_segment(self, pid: str, seg_id: str) -> None:
        def _do(p: Project):
            p.segments = [s for s in p.segments if s.id != seg_id]

        self.mutate(pid, _do)
        seg_audio = self.project_dir(pid) / "audio" / seg_id
        if seg_audio.exists():
            shutil.rmtree(seg_audio)

    def update_settings(
        self,
        pid: str,
        *,
        name: str | None = None,
        langs: list[str] | None = None,
        default_pace: str | None = None,
        voices: dict[str, str] | None = None,
        paces: dict[str, str] | None = None,
        question_template: str | None = None,
        option_template: str | None = None,
        lexicon: dict[str, dict[str, str]] | None = None,
    ) -> Project:
        def _do(p: Project):
            if name is not None and name.strip():
                p.name = name.strip()
            if langs is not None:
                bad = [c for c in langs if c not in LANGUAGES]
                if bad:
                    raise ValueError(f"unknown lang codes: {bad}")
                p.langs = list(langs)
            if default_pace is not None:
                if default_pace not in PACE_PHRASES:
                    raise ValueError(f"unknown pace: {default_pace}")
                p.default_pace = default_pace
            if voices is not None:
                p.voices = {k: v for k, v in voices.items() if k in LANGUAGES}
            if paces is not None:
                bad_p = [(k, v) for k, v in paces.items()
                         if k not in LANGUAGES or v not in PACE_PHRASES]
                if bad_p:
                    raise ValueError(f"invalid paces: {bad_p}")
                p.paces = dict(paces)
            # Templates: changing them invalidates every segment that uses them,
            # since the rendered text the model translates+speaks has changed.
            if question_template is not None and question_template != p.question_template:
                p.question_template = question_template
                for s in p.segments:
                    if s.type == "question" and s.use_template:
                        s.translations = {}
                        s.current_takes = {}
            if option_template is not None and option_template != p.option_template:
                p.option_template = option_template
                for s in p.segments:
                    if s.type == "option" and s.use_template:
                        s.translations = {}
                        s.current_takes = {}
            # Lexicon changes affect every effective text everywhere → blow
            # away cached translations + takes for the whole project.
            if lexicon is not None and lexicon != p.lexicon:
                p.lexicon = {
                    scope: dict(entries or {})
                    for scope, entries in lexicon.items()
                }
                for s in p.segments:
                    s.translations = {}
                    s.current_takes = {}

        return self.mutate(pid, _do)

    # ------------------------------------------------------------------
    # Rotations
    # ------------------------------------------------------------------

    def enable_rotations(
        self,
        pid: str,
        count: int,
        *,
        seed: int | None = None,
        lock_last_as_nota: bool = False,
    ) -> Project:
        """Turn on option-order randomization with `count` rotations.

        If `lock_last_as_nota=True`, the last option in declared order is
        marked `lock_at_end` first (the typical NOTA case). Then `count`
        distinct orderings are computed and persisted; non-canonical
        rotations have their cached translations + current_takes cleared
        (canonical r0 keeps its existing audio).
        """
        if count < 1:
            raise ValueError("rotation count must be >= 1")

        def _do(p: Project) -> None:
            options = [s for s in p.segments if s.type == "option"]
            if lock_last_as_nota and options:
                for s in options[:-1]:
                    s.lock_at_end = False
                options[-1].lock_at_end = True
            p.rotation_count = count
            p.rotation_seed = seed
            p.rotations = compute_rotations(options, count, seed=seed)
            _prune_stale_rotation_state(p)

        return self.mutate(pid, _do)

    def disable_rotations(self, pid: str) -> Project:
        """Collapse back to a single (canonical) rotation."""
        def _do(p: Project) -> None:
            p.rotation_count = 1
            p.rotations = []
            p.rotation_seed = None
            _prune_stale_rotation_state(p)
        return self.mutate(pid, _do)

    def reshuffle_rotations(self, pid: str, seed: int | None = None) -> Project:
        """Recompute rotation orderings (useful after the user adds a new
        option or wants a fresh shuffle)."""
        def _do(p: Project) -> None:
            options = [s for s in p.segments if s.type == "option"]
            if seed is not None:
                p.rotation_seed = seed
            p.rotations = compute_rotations(options, p.rotation_count, seed=p.rotation_seed)
            _prune_stale_rotation_state(p)
        return self.mutate(pid, _do)

    def set_segment_lock_at_end(
        self, pid: str, seg_id: str, lock: bool
    ) -> Segment:
        """Toggle whether this option is pinned to the end of every rotation.
        If rotations are active, recompute them so the change takes effect."""
        def _do(p: Project) -> None:
            seg = p.find_segment(seg_id)
            if seg.type != "option":
                raise ValueError("lock_at_end only applies to option segments")
            if bool(lock) == seg.lock_at_end:
                return
            seg.lock_at_end = bool(lock)
            if p.rotation_count > 1:
                options = [s for s in p.segments if s.type == "option"]
                p.rotations = compute_rotations(options, p.rotation_count, seed=p.rotation_seed)
                _prune_stale_rotation_state(p)
        self.mutate(pid, _do)
        return self.load(pid).find_segment(seg_id)

    # ------------------------------------------------------------------
    # Templates / per-segment flags (existing)
    # ------------------------------------------------------------------

    def set_segment_use_template(
        self, pid: str, seg_id: str, use: bool
    ) -> Segment:
        """Toggle whether the project template wraps this segment.

        Changing the flag changes the effective text → invalidate cached
        translations + current takes for this segment. Old attempt files
        on disk stay (history)."""
        def _do(p: Project):
            seg = p.find_segment(seg_id)
            if bool(use) == seg.use_template:
                return
            seg.use_template = bool(use)
            seg.translations = {}
            seg.current_takes = {}

        self.mutate(pid, _do)
        return self.load(pid).find_segment(seg_id)

    # ----- attempts -----

    def list_attempts(
        self, pid: str, seg_id: str, lang: str,
        rotation_id: str = CANONICAL_ROTATION,
    ) -> list[dict]:
        out: list[dict] = []
        # Read from the rotation-aware dir first.
        ad = self.audio_dir(pid, seg_id, lang, rotation_id)
        seen: set[str] = set()
        for meta in sorted(ad.glob("*.json")) if ad.exists() else []:
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            seen.add(d.get("id", ""))
            out.append(d)
        # For r0, also surface legacy attempts that pre-date rotations.
        if rotation_id == CANONICAL_ROTATION:
            legacy = self._legacy_audio_dir(pid, seg_id, lang)
            for meta in sorted(legacy.glob("*.json")) if legacy.exists() else []:
                try:
                    d = json.loads(meta.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                if d.get("id") not in seen:
                    out.append(d)
        out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return out

    def write_attempt(
        self,
        pid: str,
        seg_id: str,
        lang: str,
        *,
        voice: str,
        pace: str,
        source_text: str,
        duration_s: float,
        model_id: str,
        mp3_src: Path,
        rotation_id: str = CANONICAL_ROTATION,
    ) -> str:
        att_id = _short_id("att")
        out = self.audio_dir(pid, seg_id, lang, rotation_id)
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{att_id}.mp3"
        shutil.copy2(mp3_src, target)
        meta = {
            "id": att_id,
            "segment_id": seg_id,
            "lang": lang,
            "rotation_id": rotation_id,
            "voice": voice,
            "pace": pace,
            "source_text": source_text,
            "duration_s": round(duration_s, 3),
            "model_id": model_id,
            "created_at": _now(),
        }
        (out / f"{att_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return att_id

    def set_current_take(
        self, pid: str, seg_id: str, lang: str, att_id: str,
        rotation_id: str = CANONICAL_ROTATION,
    ) -> Segment:
        # Verify the take exists at the rotation-aware path (with legacy fallback).
        if not self.attempt_mp3(pid, seg_id, lang, att_id, rotation_id).exists():
            raise FileNotFoundError(att_id)

        def _do(p: Project):
            seg = p.find_segment(seg_id)
            seg.current_takes.setdefault(lang, {})[rotation_id] = att_id

        self.mutate(pid, _do)
        return self.load(pid).find_segment(seg_id)

    # ----- staleness helpers -----

    @staticmethod
    def stale_langs(seg: Segment) -> list[str]:
        """Languages whose translation is missing for current english."""
        if not seg.english.strip():
            return []
        return [c for c in LANGUAGES if c not in seg.translations]
