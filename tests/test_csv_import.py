"""CSV bulk-import — schemas, grouping, error paths."""
from __future__ import annotations

import io

import pytest

from prashnam_voice.csv_import import import_csv
from prashnam_voice.projects import ProjectStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bytes(s: str) -> io.BytesIO:
    return io.BytesIO(s.encode("utf-8"))


# ---------------------------------------------------------------------------
# Poll domain
# ---------------------------------------------------------------------------


def test_poll_creates_one_project_per_group(tmp_path):
    csv_text = """\
group_id,type,english
p1,question,Who will win?
p1,option,Party A
p1,option,Party B
p2,question,Are you happy?
p2,option,Yes
p2,option,No
"""
    store = ProjectStore(tmp_path)
    result = import_csv(_bytes(csv_text), store, domain="poll", langs=["hi"])
    assert result.ok
    assert len(result.projects) == 2
    assert result.rows_consumed == 6
    p1 = result.projects[0]
    assert p1.name == "Who will win?"
    types = [s.type for s in p1.segments]
    assert types == ["question", "option", "option"]


def test_poll_uses_explicit_name_column(tmp_path):
    csv_text = """\
group_id,type,english,name
p1,question,Who will win?,My Poll
p1,option,A,
p1,option,B,
"""
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="poll", langs=["hi"])
    assert r.projects[0].name == "My Poll"


def test_poll_per_group_langs_override(tmp_path):
    csv_text = """\
group_id,type,english,langs
p1,question,Q?,hi|ta
p1,option,A,
p1,option,B,
"""
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="poll", langs=["hi", "ta", "bn"])
    assert r.projects[0].langs == ["hi", "ta"]


def test_poll_rejects_two_questions_in_one_group(tmp_path):
    csv_text = """\
group_id,type,english
p1,question,Q1
p1,question,Q2
p1,option,A
"""
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="poll", langs=["hi"])
    assert len(r.projects) == 0
    assert any("question" in e.message for e in r.errors)


def test_poll_rejects_missing_options(tmp_path):
    csv_text = """\
group_id,type,english
p1,question,Q?
"""
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="poll", langs=["hi"])
    assert len(r.projects) == 0
    assert any("option" in e.message for e in r.errors)


def test_poll_invalid_type_is_per_row_error(tmp_path):
    csv_text = """\
group_id,type,english
p1,question,Q?
p1,banana,oops
p1,option,A
"""
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="poll", langs=["hi"])
    # Bad row dropped, project still creatable from question + option.
    assert len(r.projects) == 1
    assert any("banana" in e.message for e in r.errors)


# ---------------------------------------------------------------------------
# Announcement domain
# ---------------------------------------------------------------------------


def test_announcement_creates_one_project_with_multiple_bodies(tmp_path):
    csv_text = """\
group_id,english
ann1,Hello.
ann1,World.
ann1,Goodbye.
"""
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="announcement", langs=["hi"])
    assert r.ok
    assert len(r.projects) == 1
    assert all(s.type == "body" for s in r.projects[0].segments)
    assert len(r.projects[0].segments) == 3


def test_announcement_missing_english_is_error(tmp_path):
    csv_text = """\
group_id,english
ann1,
"""
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="announcement", langs=["hi"])
    assert any("english" in e.message for e in r.errors)
    assert len(r.projects) == 0


# ---------------------------------------------------------------------------
# General hygiene
# ---------------------------------------------------------------------------


def test_blank_and_comment_rows_are_skipped(tmp_path):
    csv_text = """\
group_id,type,english
p1,question,Q?
,,

# a comment row
p1,option,A
p1,option,B
"""
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="poll", langs=["hi"])
    assert len(r.projects) == 1
    assert r.rows_consumed == 3


def test_missing_header_is_fatal(tmp_path):
    csv_text = "group_id,english\np1,A\n"
    store = ProjectStore(tmp_path)
    r = import_csv(_bytes(csv_text), store, domain="poll", langs=["hi"])
    assert len(r.projects) == 0
    assert r.errors and r.errors[0].line_no == 0


def test_unsupported_domain_raises(tmp_path):
    store = ProjectStore(tmp_path)
    with pytest.raises(ValueError):
        import_csv(_bytes("group_id\n"), store, domain="ivr", langs=["hi"])


def test_examples_polls_csv_imports_cleanly(tmp_path):
    """The shipped examples/polls.csv should round-trip without errors."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    sample = repo_root / "examples" / "polls.csv"
    if not sample.exists():
        pytest.skip("examples/polls.csv not present")
    store = ProjectStore(tmp_path)
    r = import_csv(sample, store, domain="poll")
    assert r.ok, r.errors
    assert len(r.projects) >= 1
