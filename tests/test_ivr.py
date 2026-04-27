"""IVR domain pack: data model, edges, positions, start segment, validation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from prashnam_voice import app_config, domains as domains_mod
from prashnam_voice.projects import (
    ALL_EDGE_KEYS,
    DTMF_KEYS,
    SPECIAL_EDGE_KEYS,
    ProjectStore,
)
from prashnam_voice.server.app import build_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    app_config.set_config_path(tmp_path / "config.json")
    yield TestClient(build_app(out_root=tmp_path / "out", projects_root=tmp_path / "projects"))
    app_config.set_config_path(None)


# ---------------------------------------------------------------------------
# Domain registration
# ---------------------------------------------------------------------------


def test_ivr_domain_registered():
    pack = domains_mod.get("ivr")
    types = {s.name for s in pack.segment_types}
    assert types == {"prompt", "menu", "response", "bridge", "terminator"}


def test_ivr_validate_rejects_dangling_edges(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    a = store.add_segment(proj.id, "menu", "Press 1 for X")
    # Plant an edge to a non-existent target.
    store.mutate(proj.id, lambda p: p.find_segment(a.id).edges.update({"1": "ghost"}))
    proj = store.load(proj.id)
    errs = domains_mod.get("ivr").validate(proj)
    assert any("ghost" in e for e in errs)


def test_ivr_validate_warns_on_empty_menu(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    store.add_segment(proj.id, "menu", "Lonely menu")
    proj = store.load(proj.id)
    errs = domains_mod.get("ivr").validate(proj)
    assert any("dead end" in e for e in errs)


# ---------------------------------------------------------------------------
# Edge mutator
# ---------------------------------------------------------------------------


def test_set_edge_creates_and_clears(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    src = store.add_segment(proj.id, "menu", "menu")
    dst = store.add_segment(proj.id, "response", "you pressed 1")

    seg = store.set_segment_edge(proj.id, src.id, "1", dst.id)
    assert seg.edges == {"1": dst.id}

    cleared = store.set_segment_edge(proj.id, src.id, "1", None)
    assert "1" not in cleared.edges


def test_set_edge_rejects_unknown_key(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    src = store.add_segment(proj.id, "menu", "m")
    dst = store.add_segment(proj.id, "response", "r")
    with pytest.raises(ValueError):
        store.set_segment_edge(proj.id, src.id, "Z", dst.id)


def test_set_edge_rejects_self_loop(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    src = store.add_segment(proj.id, "menu", "m")
    with pytest.raises(ValueError):
        store.set_segment_edge(proj.id, src.id, "1", src.id)


def test_set_edge_rejects_unknown_target(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    src = store.add_segment(proj.id, "menu", "m")
    with pytest.raises(KeyError):
        store.set_segment_edge(proj.id, src.id, "1", "nonexistent")


def test_deleting_segment_drops_edges_and_start(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    a = store.add_segment(proj.id, "menu", "m")
    b = store.add_segment(proj.id, "response", "r")
    store.set_segment_edge(proj.id, a.id, "1", b.id)
    store.set_start_segment(proj.id, b.id)
    store.delete_segment(proj.id, b.id)
    p = store.load(proj.id)
    seg = p.find_segment(a.id)
    assert "1" not in seg.edges
    assert p.start_segment_id == ""


# ---------------------------------------------------------------------------
# Position + start
# ---------------------------------------------------------------------------


def test_set_segment_position(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    seg = store.add_segment(proj.id, "prompt", "hi")
    moved = store.set_segment_position(proj.id, seg.id, 320, 240)
    assert moved.x == 320 and moved.y == 240


def test_resolve_start_segment(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["en"], domain="ivr")
    pr = store.add_segment(proj.id, "prompt", "first")
    me = store.add_segment(proj.id, "menu", "branch")
    proj = store.load(proj.id)
    # No pin → first prompt wins.
    assert proj.resolve_start_segment().id == pr.id
    # Pin → that wins.
    store.set_start_segment(proj.id, me.id)
    proj = store.load(proj.id)
    assert proj.resolve_start_segment().id == me.id


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


def test_ivr_keys_endpoint(client):
    r = client.get("/api/ivr-keys")
    assert r.status_code == 200
    body = r.json()
    assert set(body["dtmf"]) == set(DTMF_KEYS)
    assert set(body["special"]) == set(SPECIAL_EDGE_KEYS)


def test_create_ivr_project_and_wire(client):
    r = client.post("/api/projects",
                    json={"name": "IVR", "domain": "ivr", "langs": ["en"]})
    assert r.status_code == 200
    pid = r.json()["id"]
    a = client.post(f"/api/projects/{pid}/segments",
                    json={"type": "menu", "english": "Press 1 for X"}).json()["segment_id"]
    b = client.post(f"/api/projects/{pid}/segments",
                    json={"type": "response", "english": "You pressed 1"}).json()["segment_id"]

    r = client.patch(f"/api/projects/{pid}/segments/{a}/edge",
                     json={"key": "1", "target": b})
    assert r.status_code == 200
    assert r.json()["segment"]["edges"] == {"1": b}

    # Clear it
    r = client.patch(f"/api/projects/{pid}/segments/{a}/edge",
                     json={"key": "1", "target": None})
    assert "1" not in r.json()["segment"]["edges"]


def test_ivr_segment_position_endpoint(client):
    r = client.post("/api/projects",
                    json={"name": "IVR", "domain": "ivr", "langs": ["en"]})
    pid = r.json()["id"]
    sid = client.post(f"/api/projects/{pid}/segments",
                      json={"type": "prompt", "english": "hi"}).json()["segment_id"]
    r = client.patch(f"/api/projects/{pid}/segments/{sid}/position",
                     json={"x": 100.5, "y": 200.5})
    assert r.status_code == 200
    seg = r.json()["segment"]
    assert seg["x"] == 100.5 and seg["y"] == 200.5


def test_start_segment_endpoint(client):
    r = client.post("/api/projects",
                    json={"name": "IVR", "domain": "ivr", "langs": ["en"]})
    pid = r.json()["id"]
    sid = client.post(f"/api/projects/{pid}/segments",
                      json={"type": "prompt", "english": "hi"}).json()["segment_id"]
    r = client.patch(f"/api/projects/{pid}/start-segment", json={"segment_id": sid})
    assert r.status_code == 200
    assert r.json()["start_segment_id"] == sid
    # Clear it.
    r = client.patch(f"/api/projects/{pid}/start-segment", json={"segment_id": None})
    assert r.json()["start_segment_id"] == ""


def test_edge_target_must_exist(client):
    r = client.post("/api/projects",
                    json={"name": "IVR", "domain": "ivr", "langs": ["en"]})
    pid = r.json()["id"]
    sid = client.post(f"/api/projects/{pid}/segments",
                      json={"type": "menu", "english": "m"}).json()["segment_id"]
    r = client.patch(f"/api/projects/{pid}/segments/{sid}/edge",
                     json={"key": "1", "target": "ghost"})
    assert r.status_code == 404
