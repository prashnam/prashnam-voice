"""Bulk CSV import.

Two domains are supported in v1, with deliberately different schemas so
each makes sense for its use case:

  poll  →  group_id,type,english
           Rows with the same `group_id` form one polling project.
           `type` is "question" or "option" (case-insensitive). The
           project name is the first question text we see for that group;
           `name=...` is also accepted as an extra header column.

  announcement → group_id,english
           Rows with the same `group_id` form one announcement project,
           each row becomes a body segment in order.

Both schemas accept an optional `name` column — if present, it sets the
project name for that group (taken from the first row of the group).
Optional `langs` column overrides the default langs for that group only,
as a `|`-separated list (e.g. `hi|ta|en`).

Empty group_id is a fatal error. Trailing whitespace in fields is trimmed.
Comments (lines starting with `#`) are ignored. Encoding is utf-8 with a
BOM-tolerant fallback.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterable

from .config import ALL_LANG_CODES, LANGUAGES
from .projects import Project, ProjectStore, SEGMENT_TYPES, _slugify

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ImportRow:
    line_no: int                       # 1-based line in the source file
    group_id: str
    type: str                          # "question" | "option" | "body"
    english: str
    name: str | None = None
    langs: list[str] | None = None


@dataclass
class ImportError:
    line_no: int                       # 0 = header, 1 = first row
    message: str


@dataclass
class ImportResult:
    projects: list[Project]
    rows_consumed: int
    errors: list[ImportError]

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _open_text(path_or_io: Path | str | IO[str] | IO[bytes]) -> IO[str]:
    if hasattr(path_or_io, "read"):
        # Already a file-like. If it's binary, decode.
        f = path_or_io
        first = f.read(0)
        if isinstance(first, bytes):
            data = f.read().decode("utf-8-sig")
            return io.StringIO(data)
        return f                              # text mode
    p = Path(path_or_io)
    return open(p, "r", encoding="utf-8-sig", newline="")


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _parse_langs(spec: str | None) -> list[str] | None:
    if not spec:
        return None
    out = []
    for raw in spec.replace(",", "|").split("|"):
        code = raw.strip().lower()
        if code and code in LANGUAGES:
            out.append(code)
    return out or None


# ---------------------------------------------------------------------------
# Validation per domain
# ---------------------------------------------------------------------------


REQUIRED_HEADERS = {
    "poll":         {"group_id", "type", "english"},
    "announcement": {"group_id", "english"},
}


def _normalize_type(domain: str, value: str) -> str | None:
    v = value.lower().strip()
    if domain == "poll":
        if v in ("q", "question"):
            return "question"
        if v in ("o", "option", "opt"):
            return "option"
        return None
    if domain == "announcement":
        if v in ("", "b", "body"):
            return "body"
        return None
    return None


def _validate_row(domain: str, row: dict, line_no: int) -> tuple[ImportRow | None, ImportError | None]:
    gid = _norm(row.get("group_id"))
    english = _norm(row.get("english"))
    if not gid:
        return None, ImportError(line_no, "missing group_id")
    if not english:
        return None, ImportError(line_no, "missing english text")

    raw_type = _norm(row.get("type", ""))
    seg_type = _normalize_type(domain, raw_type)
    if seg_type is None:
        return None, ImportError(
            line_no,
            f"invalid type {raw_type!r} for {domain} domain",
        )
    if seg_type not in SEGMENT_TYPES:
        return None, ImportError(line_no, f"unknown segment type {seg_type!r}")

    name = _norm(row.get("name")) or None
    langs = _parse_langs(row.get("langs"))
    return ImportRow(
        line_no=line_no,
        group_id=gid,
        type=seg_type,
        english=english,
        name=name,
        langs=langs,
    ), None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def import_csv(
    source: Path | str | IO[str] | IO[bytes],
    store: ProjectStore,
    *,
    domain: str = "poll",
    langs: Iterable[str] | None = None,
) -> ImportResult:
    """Import a CSV into the given store. Returns the new projects + any
    per-row errors. Errors are non-fatal — valid rows are still imported.
    """
    if domain not in REQUIRED_HEADERS:
        raise ValueError(f"unsupported domain for import: {domain}")

    base_langs = list(langs) if langs else list(ALL_LANG_CODES)
    bad_lang = [c for c in base_langs if c not in LANGUAGES]
    if bad_lang:
        raise ValueError(f"unknown lang codes: {bad_lang}")

    f = _open_text(source)
    try:
        # Skip leading comment / blank lines before locating the header.
        # `csv.DictReader` would otherwise treat a `# ...` line as the header.
        # Lines after the header are left in place so per-row error
        # messages still point at the correct original line numbers (the
        # row-iterator below skips comment rows).
        text = f.read()
        lines = text.splitlines()
        header_idx = None
        for i, line in enumerate(lines):
            if line.strip() and not line.lstrip().startswith("#"):
                header_idx = i
                break
        if header_idx is None:
            return ImportResult([], 0, [ImportError(0, "empty file")])

        csv_stream = io.StringIO("\n".join(lines[header_idx:]))
        reader = csv.DictReader(csv_stream)
        if reader.fieldnames is None:
            return ImportResult([], 0, [ImportError(0, "empty file or missing header")])

        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        missing = REQUIRED_HEADERS[domain] - headers
        if missing:
            return ImportResult([], 0, [ImportError(
                0, f"missing required header(s): {', '.join(sorted(missing))}",
            )])

        # Group rows by group_id, preserving first-seen order.
        groups: dict[str, list[ImportRow]] = {}
        order: list[str] = []
        errors: list[ImportError] = []
        rows_consumed = 0

        # Inside the rebuilt stream, the header is at offset 1 and data
        # rows start at offset 2. Add `header_idx` to recover the original
        # file's 1-indexed line number for accurate error messages.
        for i, raw in enumerate(reader, start=2):
            if not raw or all(not _norm(v) for v in raw.values()):
                continue
            first_cell = _norm(next(iter(raw.values()), ""))
            if first_cell.startswith("#"):
                continue
            line_no = i + header_idx
            normalized = {(k or "").strip().lower(): v for k, v in raw.items()}
            row, err = _validate_row(domain, normalized, line_no)
            if err is not None:
                errors.append(err)
                continue
            assert row is not None
            rows_consumed += 1
            if row.group_id not in groups:
                groups[row.group_id] = []
                order.append(row.group_id)
            groups[row.group_id].append(row)
    finally:
        if hasattr(source, "read"):
            pass  # caller manages the handle
        else:
            f.close()

    # Build projects
    new_projects: list[Project] = []
    for gid in order:
        rows = groups[gid]
        proj = _project_from_group(store, domain, gid, rows, base_langs, errors)
        if proj is not None:
            new_projects.append(proj)

    return ImportResult(new_projects, rows_consumed, errors)


def _project_from_group(
    store: ProjectStore,
    domain: str,
    group_id: str,
    rows: list[ImportRow],
    base_langs: list[str],
    errors: list[ImportError],
) -> Project | None:
    if not rows:
        return None

    # Domain-specific structure checks
    if domain == "poll":
        questions = [r for r in rows if r.type == "question"]
        options = [r for r in rows if r.type == "option"]
        if len(questions) != 1:
            errors.append(ImportError(
                rows[0].line_no,
                f"group {group_id!r}: polls need exactly 1 question (got {len(questions)})",
            ))
            return None
        if not options:
            errors.append(ImportError(
                rows[0].line_no,
                f"group {group_id!r}: poll needs at least 1 option",
            ))
            return None

    # Pick a reasonable project name: explicit `name=` from any row (first wins),
    # else the first question text for polls / first body for announcements,
    # else the group_id.
    name = next((r.name for r in rows if r.name), None)
    if not name:
        if domain == "poll":
            name = next((r.english for r in rows if r.type == "question"), None) or group_id
        else:
            name = rows[0].english or group_id
    name = name[:80]

    # Per-row langs override (first row's wins for the project)
    chosen_langs = next((r.langs for r in rows if r.langs), None) or base_langs

    proj = store.create(name=name, langs=chosen_langs, domain=domain)

    # Add segments in source order.
    for r in rows:
        try:
            store.add_segment(proj.id, r.type, english=r.english)
        except Exception as exc:  # noqa: BLE001
            errors.append(ImportError(
                r.line_no,
                f"could not add segment to {group_id!r}: {exc}",
            ))

    return store.load(proj.id)
