"""Domain packs.

A *domain* tells the rest of the system what shape a project takes:

  - which segment types exist (poll: question + option; announcement: body)
  - which templates are meaningful (poll has both; announcement has none)
  - what counts as a valid project (poll needs a question + ≥1 option;
    announcement needs ≥1 body)
  - what defaults a freshly-created project should get

The default `poll` domain replicates today's behavior; `announcement`
demonstrates the abstraction with a flat, single-segment shape suited to
PSAs / store announcements / robocall preamble work.

Third parties can call `register(domain)` to add their own pack — the
registry is intentionally a flat dict so it's easy to introspect.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default templates per domain. Kept close to the registry so a new domain
# can ship its own without touching projects.py.
# ---------------------------------------------------------------------------

POLL_QUESTION_TEMPLATE = (
    "Namaskar, this is a call from Prashnam, an independent polling agency. "
    "{body}"
)
POLL_OPTION_TEMPLATE = "If you think {body}, then press {n}."

ANNOUNCEMENT_BODY_TEMPLATE = ""    # body speaks for itself


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentTypeSpec:
    name: str                       # "question" | "option" | "body" | …
    label: str                      # human-readable, e.g. "Option"
    addable: bool = True            # can the user add more of this type?
    deletable: bool = True
    max: int | None = None          # cap, if any (e.g. poll has 1 question)
    template_field: str | None = None
    """Project-level field that wraps this type. e.g. for poll-options it's
    `option_template`; for announcement-body it's None (no wrapper).
    """


@dataclass
class DomainPack:
    name: str                       # "poll", "announcement", …
    label: str                      # human label
    description: str
    segment_types: list[SegmentTypeSpec]
    default_templates: dict[str, str] = field(default_factory=dict)
    # Optional callable that returns a list of validation errors. Empty list
    # means valid.
    validate: Callable[["object"], list[str]] = lambda _project: []

    def segment_type(self, name: str) -> SegmentTypeSpec | None:
        for spec in self.segment_types:
            if spec.name == name:
                return spec
        return None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "segment_types": [
                {
                    "name": s.name,
                    "label": s.label,
                    "addable": s.addable,
                    "deletable": s.deletable,
                    "max": s.max,
                    "template_field": s.template_field,
                }
                for s in self.segment_types
            ],
            "default_templates": dict(self.default_templates),
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_LOCK = threading.Lock()
_DOMAINS: dict[str, DomainPack] = {}


def register(pack: DomainPack) -> None:
    with _LOCK:
        _DOMAINS[pack.name] = pack


def get(name: str) -> DomainPack:
    with _LOCK:
        if name not in _DOMAINS:
            raise KeyError(f"unknown domain: {name}")
        return _DOMAINS[name]


def all_domains() -> list[DomainPack]:
    with _LOCK:
        return list(_DOMAINS.values())


def names() -> list[str]:
    with _LOCK:
        return list(_DOMAINS.keys())


# ---------------------------------------------------------------------------
# Built-in domain packs
# ---------------------------------------------------------------------------


def _validate_poll(project) -> list[str]:
    errs: list[str] = []
    qs = [s for s in project.segments if s.type == "question"]
    opts = [s for s in project.segments if s.type == "option"]
    if len(qs) != 1:
        errs.append(f"polls need exactly 1 question (have {len(qs)})")
    if len(opts) < 1:
        errs.append("polls need at least 1 option")
    return errs


def _validate_announcement(project) -> list[str]:
    errs: list[str] = []
    bodies = [s for s in project.segments if s.type == "body"]
    if not bodies:
        errs.append("announcement needs at least 1 body segment")
    return errs


POLL = DomainPack(
    name="poll",
    label="Poll",
    description=(
        "1 question + N options. Each option carries an index used by IVR."
    ),
    segment_types=[
        SegmentTypeSpec(
            name="question", label="Question",
            addable=False, deletable=False, max=1,
            template_field="question_template",
        ),
        SegmentTypeSpec(
            name="option", label="Option",
            addable=True, deletable=True,
            template_field="option_template",
        ),
    ],
    default_templates={
        "question_template": POLL_QUESTION_TEMPLATE,
        "option_template": POLL_OPTION_TEMPLATE,
    },
    validate=_validate_poll,
)

ANNOUNCEMENT = DomainPack(
    name="announcement",
    label="Announcement",
    description=(
        "Flat list of body segments. PSAs, store announcements, "
        "robocall scripts. No per-segment numbering."
    ),
    segment_types=[
        SegmentTypeSpec(
            name="body", label="Body",
            addable=True, deletable=True,
            template_field=None,    # no wrapping by default
        ),
    ],
    default_templates={
        "question_template": "",
        "option_template": "",
    },
    validate=_validate_announcement,
)


# ---------------------------------------------------------------------------
# IVR — branching menu trees (Tier 2)
# ---------------------------------------------------------------------------


def _validate_ivr(project) -> list[str]:
    """Sanity checks for an IVR project."""
    errs: list[str] = []
    segs = list(project.segments)
    if not segs:
        errs.append("IVR projects need at least one segment")
        return errs

    ids = {s.id for s in segs}
    # Edges that point at non-existent segments
    for s in segs:
        for key, target in (s.edges or {}).items():
            if target not in ids:
                errs.append(
                    f"segment {s.id} edge {key!r} points at unknown segment {target!r}"
                )

    # Start segment, if pinned, must exist
    if project.start_segment_id and project.start_segment_id not in ids:
        errs.append(
            f"start_segment_id {project.start_segment_id!r} is not a real segment"
        )

    # Menu segments should have at least one outgoing edge to be useful;
    # warn (don't fail) when not — sometimes you're mid-edit.
    for s in segs:
        if s.type == "menu" and not s.edges:
            errs.append(
                f"menu segment {s.id} has no outgoing edges yet — "
                "callers will hit a dead end"
            )

    return errs


IVR = DomainPack(
    name="ivr",
    label="IVR menu",
    description=(
        "Interactive Voice Response menu — a graph of prompts, branching "
        "menus, responses, and terminators connected by DTMF edges."
    ),
    segment_types=[
        SegmentTypeSpec(
            name="prompt", label="Prompt",
            addable=True, deletable=True,
            template_field=None,
        ),
        SegmentTypeSpec(
            name="menu", label="Menu",
            addable=True, deletable=True,
            template_field=None,
        ),
        SegmentTypeSpec(
            name="response", label="Response",
            addable=True, deletable=True,
            template_field=None,
        ),
        SegmentTypeSpec(
            name="bridge", label="Bridge",
            addable=True, deletable=True,
            template_field=None,
        ),
        SegmentTypeSpec(
            name="terminator", label="Terminator",
            addable=True, deletable=True,
            template_field=None,
        ),
    ],
    default_templates={
        "question_template": "",
        "option_template": "",
    },
    validate=_validate_ivr,
)

register(POLL)
register(ANNOUNCEMENT)
register(IVR)
