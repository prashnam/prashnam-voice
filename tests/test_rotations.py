"""Option-order rotation feature: data, helpers, mutators, API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from prashnam_voice import app_config
from prashnam_voice.projects import (
    CANONICAL_ROTATION,
    ProjectStore,
    Segment,
    compute_rotations,
    effective_text,
)
from prashnam_voice.server.app import build_app


# ---------------------------------------------------------------------------
# compute_rotations
# ---------------------------------------------------------------------------


def _opts(*ids_with_lock: tuple[str, bool]) -> list[Segment]:
    return [
        Segment(id=i, type="option", english=i, lock_at_end=lock)
        for i, lock in ids_with_lock
    ]


def test_compute_rotations_canonical_first():
    opts = _opts(("a", False), ("b", False), ("c", False))
    out = compute_rotations(opts, count=3, seed=42)
    assert out[0] == ["a", "b", "c"]
    assert len(out) == 3


def test_compute_rotations_locked_always_last():
    opts = _opts(("a", False), ("b", False), ("c", True))   # c is NOTA
    out = compute_rotations(opts, count=4, seed=7)
    for r in out:
        assert r[-1] == "c", f"locked option must be last, got {r}"


def test_compute_rotations_distinct_orderings():
    opts = _opts(("a", False), ("b", False), ("c", False), ("d", True))
    out = compute_rotations(opts, count=4, seed=1)
    keys = {tuple(r) for r in out}
    assert len(keys) == len(out)


def test_compute_rotations_seed_is_deterministic():
    opts = _opts(("a", False), ("b", False), ("c", False), ("d", True))
    a = compute_rotations(opts, 4, seed=99)
    b = compute_rotations(opts, 4, seed=99)
    assert a == b


def test_compute_rotations_falls_back_when_too_few_perms():
    opts = _opts(("a", False), ("b", True))    # only 1! free permutations
    out = compute_rotations(opts, count=5, seed=0)
    # Can produce at most 1 distinct ordering (just the canonical).
    assert len(out) == 1


def test_compute_rotations_count_one_returns_canonical_only():
    opts = _opts(("a", False), ("b", False))
    out = compute_rotations(opts, 1)
    assert out == [["a", "b"]]


# ---------------------------------------------------------------------------
# effective_text per rotation
# ---------------------------------------------------------------------------


def test_effective_text_uses_rotation_position(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("R", ["hi"], domain="poll")
    store.add_segment(proj.id, "question", "Q?")
    a = store.add_segment(proj.id, "option", "Party A")
    b = store.add_segment(proj.id, "option", "Party B")
    c = store.add_segment(proj.id, "option", "Party C")
    proj = store.enable_rotations(proj.id, count=3, seed=1, lock_last_as_nota=False)

    seg_a = proj.find_segment(a.id)
    # In rotation r0, a is at position 1
    e0 = effective_text(proj, seg_a, lang="hi", rotation_id="r0")
    assert "press one" in e0
    # If a is at a different position in r1, the wrapped text should reflect it.
    pos_in_r1 = proj.option_position_in_rotation(a.id, "r1")
    e1 = effective_text(proj, seg_a, lang="hi", rotation_id="r1")
    if pos_in_r1 == 1:
        assert "press one" in e1
    elif pos_in_r1 == 2:
        assert "press two" in e1
    elif pos_in_r1 == 3:
        assert "press three" in e1


# ---------------------------------------------------------------------------
# ProjectStore mutators
# ---------------------------------------------------------------------------


def test_enable_with_nota_locks_last_option(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"], domain="poll")
    store.add_segment(proj.id, "question", "Q?")
    store.add_segment(proj.id, "option", "A")
    store.add_segment(proj.id, "option", "B")
    nota = store.add_segment(proj.id, "option", "Don't know")

    proj = store.enable_rotations(proj.id, count=3, seed=42, lock_last_as_nota=True)
    seg_nota = proj.find_segment(nota.id)
    assert seg_nota.lock_at_end is True
    # Every rotation has nota at the end.
    for ordering in proj.rotations:
        assert ordering[-1] == nota.id


def test_disable_collapses_to_canonical(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"], domain="poll")
    store.add_segment(proj.id, "question", "Q?")
    store.add_segment(proj.id, "option", "A")
    store.add_segment(proj.id, "option", "B")
    store.enable_rotations(proj.id, count=3, seed=7)
    proj = store.disable_rotations(proj.id)
    assert proj.rotation_count == 1
    assert proj.rotations == []
    assert proj.rotation_ids() == [CANONICAL_ROTATION]


def test_reshuffle_changes_orderings(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"], domain="poll")
    store.add_segment(proj.id, "question", "Q?")
    for letter in "abcd":
        store.add_segment(proj.id, "option", letter)
    store.enable_rotations(proj.id, count=4, seed=1)
    before = store.load(proj.id).rotations
    proj_after = store.reshuffle_rotations(proj.id, seed=999)
    # First rotation (canonical) is always identical; later ones should differ.
    assert proj_after.rotations[0] == before[0]
    assert proj_after.rotations[1:] != before[1:]


def test_lock_at_end_rejected_for_non_options(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"], domain="poll")
    q = store.add_segment(proj.id, "question", "Q?")
    with pytest.raises(ValueError):
        store.set_segment_lock_at_end(proj.id, q.id, True)


def test_toggling_lock_recomputes_rotations(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"], domain="poll")
    store.add_segment(proj.id, "question", "Q?")
    store.add_segment(proj.id, "option", "A")
    store.add_segment(proj.id, "option", "B")
    nota = store.add_segment(proj.id, "option", "DK")
    store.enable_rotations(proj.id, count=3, seed=1, lock_last_as_nota=False)

    # Without lock, last option might appear at any position.
    store.set_segment_lock_at_end(proj.id, nota.id, True)
    proj = store.load(proj.id)
    for r in proj.rotations:
        assert r[-1] == nota.id


# ---------------------------------------------------------------------------
# Migration of legacy current_takes / translations shapes
# ---------------------------------------------------------------------------


def test_legacy_str_shape_migrates_to_r0(tmp_path):
    """Old projects stored translations as {lang: str}. Loading should
    migrate to {lang: {r0: str}}."""
    import json
    pdir = tmp_path / "legacy"
    pdir.mkdir()
    payload = {
        "id": "legacy",
        "name": "L",
        "created_at": "2026-01-01T00:00:00.000000+00:00",
        "updated_at": "2026-01-01T00:00:00.000000+00:00",
        "langs": ["hi"],
        "segments": [{
            "id": "seg1",
            "type": "question",
            "english": "Q?",
            "translations": {"hi": "क्या?"},
            "current_takes": {"hi": "att_old"},
        }],
    }
    (pdir / "project.json").write_text(json.dumps(payload), encoding="utf-8")
    store = ProjectStore(tmp_path)
    p = store.load("legacy")
    seg = p.segments[0]
    assert seg.translations == {"hi": {"r0": "क्या?"}}
    assert seg.current_takes == {"hi": {"r0": "att_old"}}


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    app_config.set_config_path(tmp_path / "config.json")
    yield TestClient(build_app(out_root=tmp_path / "out", projects_root=tmp_path / "projects"))
    app_config.set_config_path(None)


def test_enable_rotations_endpoint(client):
    r = client.post("/api/projects",
                    json={"name": "P", "langs": ["hi"], "domain": "poll"})
    pid = r.json()["id"]
    client.post(f"/api/projects/{pid}/segments", json={"type": "question", "english": "Q?"})
    # Need >= 3 free options for 3 distinct rotations (3! = 6 free perms).
    for letter in "ABC":
        client.post(f"/api/projects/{pid}/segments",
                    json={"type": "option", "english": letter})
    client.post(f"/api/projects/{pid}/segments",
                json={"type": "option", "english": "DK"})

    r = client.post(f"/api/projects/{pid}/rotations/enable",
                    json={"count": 3, "seed": 42, "lock_last_as_nota": True})
    assert r.status_code == 200
    p = r.json()
    assert p["rotation_count"] == 3
    assert len(p["rotations"]) == 3
    # NOTA (last option) locked
    assert any(s["lock_at_end"] for s in p["segments"] if s["type"] == "option")


def test_enable_rotations_rejects_count_one(client):
    r = client.post("/api/projects",
                    json={"name": "P", "langs": ["hi"], "domain": "poll"})
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/rotations/enable",
                    json={"count": 1})
    assert r.status_code == 400


def test_disable_rotations_endpoint(client):
    r = client.post("/api/projects",
                    json={"name": "P", "langs": ["hi"], "domain": "poll"})
    pid = r.json()["id"]
    client.post(f"/api/projects/{pid}/segments", json={"type": "option", "english": "A"})
    client.post(f"/api/projects/{pid}/segments", json={"type": "option", "english": "B"})
    client.post(f"/api/projects/{pid}/rotations/enable", json={"count": 3, "seed": 1})
    r = client.post(f"/api/projects/{pid}/rotations/disable")
    assert r.status_code == 200
    assert r.json()["rotation_count"] == 1


def test_lock_endpoint(client):
    r = client.post("/api/projects",
                    json={"name": "P", "langs": ["hi"], "domain": "poll"})
    pid = r.json()["id"]
    client.post(f"/api/projects/{pid}/segments", json={"type": "option", "english": "A"})
    seg2 = client.post(f"/api/projects/{pid}/segments",
                       json={"type": "option", "english": "B"}).json()["segment_id"]
    r = client.patch(f"/api/projects/{pid}/segments/{seg2}/lock",
                     json={"lock_at_end": True})
    assert r.status_code == 200
    assert r.json()["segment"]["lock_at_end"] is True


def test_reshuffle_endpoint(client):
    r = client.post("/api/projects",
                    json={"name": "P", "langs": ["hi"], "domain": "poll"})
    pid = r.json()["id"]
    for letter in "abcde":
        client.post(f"/api/projects/{pid}/segments",
                    json={"type": "option", "english": letter})
    client.post(f"/api/projects/{pid}/rotations/enable", json={"count": 3, "seed": 1})
    before = client.get(f"/api/projects/{pid}").json()["rotations"]
    r = client.post(f"/api/projects/{pid}/rotations/reshuffle", json={"seed": 999})
    assert r.status_code == 200
    after = r.json()["rotations"]
    # Canonical first row is always equal; later ones differ.
    assert after[0] == before[0]
    assert after[1:] != before[1:]
