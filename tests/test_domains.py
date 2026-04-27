"""Domain pack registry, validation, and project-creation behaviour."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from prashnam_voice import app_config
from prashnam_voice import domains as domains_mod
from prashnam_voice.projects import ProjectStore
from prashnam_voice.server.app import build_app


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_builtin_domains_registered():
    names = domains_mod.names()
    assert "poll" in names
    assert "announcement" in names


def test_poll_domain_segment_types():
    pack = domains_mod.get("poll")
    types = {s.name: s for s in pack.segment_types}
    assert "question" in types and types["question"].max == 1
    assert "option" in types and types["option"].addable is True
    # Question is the seed segment (max=1, not addable)
    assert types["question"].addable is False


def test_announcement_domain_segment_types():
    pack = domains_mod.get("announcement")
    types = {s.name: s for s in pack.segment_types}
    assert "body" in types and types["body"].addable is True
    assert "question" not in types and "option" not in types


def test_unknown_domain_raises():
    with pytest.raises(KeyError):
        domains_mod.get("nope")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_poll_validation(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"], domain="poll")
    pack = domains_mod.get("poll")
    # Just-created project still needs the question + option auto-creation
    # (the editor does that in JS); validation should report missing pieces.
    errs = pack.validate(proj)
    assert any("question" in e for e in errs)
    assert any("option" in e for e in errs)

    store.add_segment(proj.id, "question", "Q?")
    store.add_segment(proj.id, "option", "A")
    proj = store.load(proj.id)
    assert pack.validate(proj) == []


def test_announcement_validation(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("A", ["hi"], domain="announcement")
    pack = domains_mod.get("announcement")
    assert any("body" in e for e in pack.validate(proj))
    store.add_segment(proj.id, "body", "Hello.")
    proj = store.load(proj.id)
    assert pack.validate(proj) == []


# ---------------------------------------------------------------------------
# Project creation behaviour
# ---------------------------------------------------------------------------


def test_project_create_seeds_templates_for_domain(tmp_path):
    store = ProjectStore(tmp_path)
    poll = store.create("Poll", ["hi"], domain="poll")
    ann = store.create("Ann", ["hi"], domain="announcement")
    assert "Prashnam" in poll.question_template     # poll preamble
    assert "press {n}" in poll.option_template
    # Announcement domain should NOT carry the polling preamble
    assert ann.question_template == ""
    assert ann.option_template == ""


def test_project_create_unknown_domain_raises(tmp_path):
    store = ProjectStore(tmp_path)
    with pytest.raises(ValueError):
        store.create("X", ["hi"], domain="unicorn")


def test_existing_projects_default_to_poll_on_load(tmp_path):
    """JSON files written before domain packs lacked the `domain` field;
    they should still load as poll-shaped."""
    import json
    pdir = tmp_path / "legacy-project"
    pdir.mkdir()
    payload = {
        "id": "legacy-project",
        "name": "Legacy",
        "created_at": "2026-04-27T10:00:00.000000+00:00",
        "updated_at": "2026-04-27T10:00:00.000000+00:00",
        "langs": ["hi"],
        "segments": [],
    }
    (pdir / "project.json").write_text(json.dumps(payload), encoding="utf-8")
    store = ProjectStore(tmp_path)
    p = store.load("legacy-project")
    assert p.domain == "poll"


# ---------------------------------------------------------------------------
# HTTP — adding the wrong segment type for a domain is a 400
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    app_config.set_config_path(tmp_path / "config.json")
    yield TestClient(build_app(out_root=tmp_path / "out", projects_root=tmp_path / "projects"))
    app_config.set_config_path(None)


def test_announcement_rejects_option_segment(client):
    r = client.post("/api/projects", json={"name": "Ann", "domain": "announcement", "langs": ["hi"]})
    assert r.status_code == 200
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/segments", json={"type": "option", "english": "x"})
    assert r.status_code == 400
    assert "announcement" in r.json()["detail"].lower()


def test_poll_rejects_second_question(client):
    r = client.post("/api/projects", json={"name": "P", "domain": "poll", "langs": ["hi"]})
    pid = r.json()["id"]
    client.post(f"/api/projects/{pid}/segments", json={"type": "question", "english": "Q1?"})
    r = client.post(f"/api/projects/{pid}/segments", json={"type": "question", "english": "Q2?"})
    assert r.status_code == 400


def test_domains_endpoint(client):
    r = client.get("/api/domains")
    assert r.status_code == 200
    names = [d["name"] for d in r.json()]
    assert "poll" in names and "announcement" in names
