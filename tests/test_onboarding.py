"""Onboarding endpoints + HF/Sarvam test probes."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from prashnam_voice import app_config
from prashnam_voice.onboarding import probe_hf_token, probe_sarvam_key
from prashnam_voice.server.app import build_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or ""

    def json(self):
        return self._payload


@pytest.fixture
def client(tmp_path):
    app_config.set_config_path(tmp_path / "config.json")
    yield TestClient(build_app(out_root=tmp_path / "out", projects_root=tmp_path / "projects"))
    app_config.set_config_path(None)


# ---------------------------------------------------------------------------
# HF probe
# ---------------------------------------------------------------------------


def test_hf_probe_ready_when_both_models_200():
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(200, {})
    with patch("prashnam_voice.onboarding.requests.get", fake_get):
        r = probe_hf_token("hf_realtoken")
    assert r.overall == "ready"
    assert all(m.status == "ok" for m in r.models)


def test_hf_probe_token_invalid_on_401():
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(401, {})
    with patch("prashnam_voice.onboarding.requests.get", fake_get):
        r = probe_hf_token("hf_badtoken")
    assert r.overall == "token_invalid"


def test_hf_probe_models_not_accepted_on_403():
    # First model 403 (ToS not accepted), second 200.
    calls = {"i": 0}
    def fake_get(url, headers=None, timeout=None):
        calls["i"] += 1
        return FakeResponse(403 if calls["i"] == 1 else 200, {})
    with patch("prashnam_voice.onboarding.requests.get", fake_get):
        r = probe_hf_token("hf_realtoken")
    assert r.overall == "models_not_accepted"
    needs = [m for m in r.models if m.status == "needs_acceptance"]
    assert len(needs) == 1


def test_hf_probe_empty_token_short_circuits():
    r = probe_hf_token("")
    assert r.overall == "token_invalid"


# ---------------------------------------------------------------------------
# Sarvam probe
# ---------------------------------------------------------------------------


def test_sarvam_probe_ready():
    def fake_post(url, **kw):
        return FakeResponse(200, {"translated_text": "नमस्ते"})
    with patch("prashnam_voice.onboarding.requests.post", fake_post):
        r = probe_sarvam_key("sk_real")
    assert r.overall == "ready"
    assert "नमस्ते" in r.sample


def test_sarvam_probe_key_invalid():
    def fake_post(url, **kw):
        return FakeResponse(401, {})
    with patch("prashnam_voice.onboarding.requests.post", fake_post):
        r = probe_sarvam_key("sk_bad")
    assert r.overall == "key_invalid"


def test_sarvam_probe_empty_key_short_circuits():
    r = probe_sarvam_key("")
    assert r.overall == "key_invalid"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_root_redirects_to_onboarding_when_fresh(client):
    r = client.get("/")
    assert r.status_code == 200
    # Onboarding page has the rail / wizard markup; the main app doesn't.
    assert "onboarding-shell" in r.text


def test_complete_onboarding_persists(client):
    payload = {
        "translator": "sarvam",
        "tts": "sarvam",
        "settings": {"sarvam": {"api_key": "abc"}},
    }
    r = client.post("/api/onboarding/complete", json=payload)
    assert r.status_code == 200
    # Health now reports onboarded
    h = client.get("/api/health").json()
    assert h["onboarded"] is True
    assert h["translator"] == "sarvam"
    assert h["tts"] == "sarvam"
    # Root now serves the main app
    r = client.get("/")
    assert "onboarding-shell" not in r.text
    assert "/static/app.js" in r.text  # main app loads app.js


def test_complete_onboarding_rejects_unknown_adapter(client):
    r = client.post(
        "/api/onboarding/complete",
        json={"translator": "fake", "tts": "fake", "settings": {}},
    )
    assert r.status_code == 400


def test_test_hf_endpoint_returns_structured(client):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(200, {})
    with patch("prashnam_voice.onboarding.requests.get", fake_get):
        r = client.post("/api/onboarding/test-hf", json={"token": "hf_t"})
    assert r.status_code == 200
    assert r.json()["overall"] == "ready"
    assert isinstance(r.json()["models"], list)


def test_test_sarvam_endpoint_returns_structured(client):
    def fake_post(url, **kw):
        return FakeResponse(200, {"translated_text": "नमस्ते"})
    with patch("prashnam_voice.onboarding.requests.post", fake_post):
        r = client.post("/api/onboarding/test-sarvam", json={"api_key": "sk_t"})
    assert r.status_code == 200
    assert r.json()["overall"] == "ready"
