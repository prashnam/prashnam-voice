"""Per-segment voice/pace overrides + onboarding model-download endpoints."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from prashnam_voice import app_config
from prashnam_voice.projects import ProjectStore
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
# Per-segment override mutator
# ---------------------------------------------------------------------------


def test_set_segment_overrides_voice_and_pace(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"])
    seg = store.add_segment(proj.id, "option", "A")

    # Set voice
    seg2 = store.set_segment_overrides(proj.id, seg.id, voice=("hi", "Aman"))
    assert seg2.voices == {"hi": "Aman"}
    # Set pace
    seg3 = store.set_segment_overrides(proj.id, seg.id, pace=("hi", "slow"))
    assert seg3.paces == {"hi": "slow"}
    # Set both at once
    seg4 = store.set_segment_overrides(
        proj.id, seg.id,
        voice=("hi", "Divya"), pace=("hi", "fast"),
    )
    assert seg4.voices == {"hi": "Divya"}
    assert seg4.paces == {"hi": "fast"}


def test_clear_override_with_none_value(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"])
    seg = store.add_segment(proj.id, "option", "A")
    store.set_segment_overrides(proj.id, seg.id, voice=("hi", "Aman"))
    cleared = store.set_segment_overrides(proj.id, seg.id, voice=("hi", None))
    assert "hi" not in cleared.voices


def test_voice_for_resolves_segment_then_project_then_default(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"])
    seg = store.add_segment(proj.id, "option", "A")
    proj = store.load(proj.id)
    seg = proj.find_segment(seg.id)

    # Default (no overrides): language default voice
    assert proj.voice_for("hi", seg) == "Divya"

    # Project-level override
    store.update_settings(proj.id, voices={"hi": "Aditi"})
    proj = store.load(proj.id)
    seg = proj.find_segment(seg.id)
    assert proj.voice_for("hi", seg) == "Aditi"

    # Segment-level override wins
    store.set_segment_overrides(proj.id, seg.id, voice=("hi", "Rohit"))
    proj = store.load(proj.id)
    seg = proj.find_segment(seg.id)
    assert proj.voice_for("hi", seg) == "Rohit"


def test_setting_override_invalidates_cached_take(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"])
    seg = store.add_segment(proj.id, "option", "A")
    store.mutate(proj.id, lambda p: p.find_segment(seg.id).set_take("hi", "r0", "att_x"))
    store.set_segment_overrides(proj.id, seg.id, pace=("hi", "slow"))
    s = store.load(proj.id).find_segment(seg.id)
    # Voice/pace change → audio is stale; takes for that lang are dropped.
    assert "hi" not in s.current_takes


def test_unknown_lang_or_pace_rejected(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("P", ["hi"])
    seg = store.add_segment(proj.id, "option", "A")
    with pytest.raises(ValueError):
        store.set_segment_overrides(proj.id, seg.id, voice=("zz", "Aman"))
    with pytest.raises(ValueError):
        store.set_segment_overrides(proj.id, seg.id, pace=("hi", "warp_speed"))


# ---------------------------------------------------------------------------
# /api/projects/{pid}/segments/{sid}/override
# ---------------------------------------------------------------------------


def test_override_endpoint_set_voice(client):
    pid = client.post("/api/projects", json={"name": "P", "domain": "poll"}).json()["id"]
    sid = client.post(f"/api/projects/{pid}/segments",
                      json={"type": "option", "english": "A"}).json()["segment_id"]
    r = client.patch(f"/api/projects/{pid}/segments/{sid}/override",
                     json={"lang": "hi", "voice": "Aman"})
    assert r.status_code == 200
    assert r.json()["segment"]["voices"] == {"hi": "Aman"}


def test_override_endpoint_clear_voice_with_null(client):
    pid = client.post("/api/projects", json={"name": "P", "domain": "poll"}).json()["id"]
    sid = client.post(f"/api/projects/{pid}/segments",
                      json={"type": "option", "english": "A"}).json()["segment_id"]
    client.patch(f"/api/projects/{pid}/segments/{sid}/override",
                 json={"lang": "hi", "voice": "Aman"})
    r = client.patch(f"/api/projects/{pid}/segments/{sid}/override",
                     json={"lang": "hi", "voice": None})
    assert r.status_code == 200
    assert "hi" not in r.json()["segment"]["voices"]


def test_override_endpoint_unknown_lang_400(client):
    pid = client.post("/api/projects", json={"name": "P", "domain": "poll"}).json()["id"]
    sid = client.post(f"/api/projects/{pid}/segments",
                      json={"type": "option", "english": "A"}).json()["segment_id"]
    r = client.patch(f"/api/projects/{pid}/segments/{sid}/override",
                     json={"lang": "zz", "voice": "Aman"})
    assert r.status_code == 400


def test_override_endpoint_requires_a_field(client):
    pid = client.post("/api/projects", json={"name": "P", "domain": "poll"}).json()["id"]
    sid = client.post(f"/api/projects/{pid}/segments",
                      json={"type": "option", "english": "A"}).json()["segment_id"]
    r = client.patch(f"/api/projects/{pid}/segments/{sid}/override",
                     json={"lang": "hi"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/voices
# ---------------------------------------------------------------------------


def test_voices_endpoint_returns_per_lang_pool(client):
    r = client.get("/api/voices")
    assert r.status_code == 200
    body = r.json()
    # Default config = local-ai4bharat → per-language pool from the model card.
    assert "Divya" in body["hi"]
    assert "Jaya"  in body["ta"]


# ---------------------------------------------------------------------------
# Onboarding model-download endpoints (no actual network — heavily mocked)
# ---------------------------------------------------------------------------


def test_download_progress_reports_idle_initially(client):
    # Replace the global tracker with a fresh empty one to be deterministic.
    from prashnam_voice import onboarding as ob
    ob._download_job = ob.DownloadJob()
    r = client.get("/api/onboarding/download-progress")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_start_download_returns_started_flag(client):
    from prashnam_voice import onboarding as ob
    ob._download_job = ob.DownloadJob()
    # Stub the inner runner so this test doesn't actually hit Hugging Face.
    with patch.object(ob, "_run_downloads", lambda *a, **kw: None):
        r = client.post("/api/onboarding/download-models", json={"token": "hf_dummy"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["started"] is True


def test_already_running_download_returns_started_false(client):
    from prashnam_voice import onboarding as ob
    ob._download_job = ob.DownloadJob(state="running")
    r = client.post("/api/onboarding/download-models", json={"token": None})
    assert r.status_code == 200
    assert r.json()["started"] is False
    # Reset state so we don't poison sibling tests.
    ob._download_job = ob.DownloadJob()
