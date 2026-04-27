"""Smoke tests for the FastAPI app that don't require the heavy models."""
import pytest
from fastapi.testclient import TestClient

from prashnam_voice.server.app import build_app


@pytest.fixture
def client(tmp_path):
    return TestClient(build_app(out_root=tmp_path / "out", projects_root=tmp_path / "projects"))


def test_languages_endpoint(client):
    r = client.get("/api/languages")
    assert r.status_code == 200
    payload = r.json()
    codes = [item["code"] for item in payload]
    # 23 langs total; en + hi at the top of the list.
    assert len(codes) == 23
    assert codes[:2] == ["en", "hi"]
    # Spot-check a couple of newly added ones.
    for must_include in ("as", "ur", "ne", "sa", "mai", "brx", "mni", "sat"):
        assert must_include in codes


def test_paces_endpoint(client):
    r = client.get("/api/paces")
    assert r.status_code == 200
    p = r.json()
    assert "moderate" in p["options"]
    assert p["default"] in p["options"]


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "prashnam-voice" in r.text


def test_unknown_job_404(client):
    r = client.get("/api/jobs/deadbeef")
    assert r.status_code == 404


def test_project_crud_no_models_needed(client):
    # list empty
    r = client.get("/api/projects"); assert r.status_code == 200; assert r.json() == []

    # create
    r = client.post("/api/projects", json={"name": "Election 2026", "langs": ["hi", "ta"]})
    assert r.status_code == 200
    p = r.json()
    pid = p["id"]
    assert p["name"] == "Election 2026"
    assert p["langs"] == ["hi", "ta"]
    assert p["segments"] == []

    # add a segment
    r = client.post(f"/api/projects/{pid}/segments",
                    json={"type": "question", "english": "Who will win?"})
    assert r.status_code == 200
    sid = r.json()["segment_id"]

    r = client.get(f"/api/projects/{pid}")
    assert len(r.json()["segments"]) == 1
    assert r.json()["segments"][0]["english"] == "Who will win?"

    # edit english
    r = client.patch(f"/api/projects/{pid}/segments/{sid}",
                     json={"english": "Who will lose?"})
    assert r.status_code == 200
    assert r.json()["segment"]["english"] == "Who will lose?"

    # delete segment
    r = client.delete(f"/api/projects/{pid}/segments/{sid}")
    assert r.status_code == 200
    assert r.json()["project"]["segments"] == []

    # delete project
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200


def test_legacy_generate_validates_input(client):
    r = client.post("/api/generate", json={"question": "", "options": ["a"]})
    assert r.status_code == 400
