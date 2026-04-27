"""Public API surface — every name in `prashnam_voice.public.__all__` must
resolve, and a smoke test of the README quick-start runs end-to-end against
a temp project store (no model calls)."""
from __future__ import annotations

from pathlib import Path

import prashnam_voice.public as P


def test_every_exported_name_resolves():
    for name in P.__all__:
        assert hasattr(P, name), f"public name {name!r} missing"


def test_quick_start_creates_project_and_segments(tmp_path):
    store = P.ProjectStore(tmp_path)
    proj = store.create("Smoke", langs=["en", "hi"], domain="poll")
    assert proj.domain == "poll"
    store.add_segment(proj.id, "question", "Who will win?")
    store.add_segment(proj.id, "option", "A")
    store.add_segment(proj.id, "option", "B")

    proj = store.load(proj.id)
    types = [s.type for s in proj.segments]
    assert types == ["question", "option", "option"]


def test_announcement_domain_via_public_api(tmp_path):
    store = P.ProjectStore(tmp_path)
    proj = store.create("PSA", langs=["en"], domain="announcement")
    store.add_segment(proj.id, "body", "Welcome.")
    proj = store.load(proj.id)
    assert [s.type for s in proj.segments] == ["body"]


def test_register_custom_domain(tmp_path):
    """Third-party can register a domain pack and use it."""
    pack = P.DomainPack(
        name="test-domain-x",
        label="Test X",
        description="from the public-API test",
        segment_types=[
            P.SegmentTypeSpec("prompt", "Prompt", addable=True, deletable=True),
        ],
        default_templates={"question_template": "", "option_template": ""},
    )
    P.register_domain(pack)
    assert P.get_domain("test-domain-x").name == "test-domain-x"

    store = P.ProjectStore(tmp_path)
    proj = store.create("X", langs=["en"], domain="test-domain-x")
    assert proj.domain == "test-domain-x"


def test_numerals_helper_exposed():
    out = P.numerals_to_words("press 1 in 2026")
    assert "one" in out and "two thousand" in out


def test_csv_import_via_public_api(tmp_path):
    csv_text = b"group_id,type,english\np1,question,Q?\np1,option,A\n"
    store = P.ProjectStore(tmp_path)
    import io
    r = P.import_csv(io.BytesIO(csv_text), store, domain="poll", langs=["en"])
    assert r.ok
    assert len(r.projects) == 1
