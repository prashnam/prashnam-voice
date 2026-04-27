"""Adapter registry, contracts, config, and Sarvam adapter (HTTP mocked)."""
from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest

from prashnam_voice import adapters as registry
from prashnam_voice import app_config
from prashnam_voice.adapters.base import (
    AdapterError,
    Setting,
    TranslatorAdapter,
    TTSAdapter,
    Voice,
)
from prashnam_voice.adapters.sarvam.translator import SarvamTranslator
from prashnam_voice.adapters.sarvam.tts import SarvamTTS


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_local_adapters_register_themselves():
    registry.ensure_builtins_loaded()
    names_t = {a.name for a in registry.list_translators()}
    names_a = {a.name for a in registry.list_tts()}
    assert "local-ai4bharat" in names_t
    assert "local-ai4bharat" in names_a


def test_sarvam_adapters_register_themselves():
    registry.ensure_builtins_loaded()
    assert any(a.name == "sarvam" for a in registry.list_translators())
    assert any(a.name == "sarvam" for a in registry.list_tts())


def test_get_translator_unknown_raises():
    registry.ensure_builtins_loaded()
    with pytest.raises(KeyError):
        registry.get_translator("nope")


def test_local_adapters_implement_protocol():
    registry.ensure_builtins_loaded()
    t = registry.get_translator("local-ai4bharat")
    a = registry.get_tts("local-ai4bharat")
    assert isinstance(t, TranslatorAdapter)
    assert isinstance(a, TTSAdapter)


# ---------------------------------------------------------------------------
# AppConfig
# ---------------------------------------------------------------------------


def test_app_config_round_trip(tmp_path):
    app_config.set_config_path(tmp_path / "config.json")
    try:
        cfg = app_config.load()
        assert cfg.translator.name == "local-ai4bharat"
        assert cfg.onboarded is False

        cfg.translator.name = "sarvam"
        cfg.translator.all_settings["sarvam"] = {"api_key": "abc123"}
        cfg.onboarded = True
        app_config.save(cfg)

        # Re-load and verify
        app_config.set_config_path(tmp_path / "config.json")
        reloaded = app_config.load()
        assert reloaded.translator.name == "sarvam"
        assert reloaded.translator.all_settings["sarvam"]["api_key"] == "abc123"
        assert reloaded.onboarded is True
    finally:
        app_config.set_config_path(None)


def test_app_config_settings_for_returns_isolated_copy(tmp_path):
    app_config.set_config_path(tmp_path / "config.json")
    try:
        cfg = app_config.load()
        cfg.translator.all_settings["sarvam"] = {"api_key": "key1"}
        cfg.translator.all_settings["elevenlabs"] = {"api_key": "key2"}
        app_config.save(cfg)
        s = cfg.translator.settings_for("sarvam")
        s["api_key"] = "tampered"   # mutating the returned dict shouldn't leak
        assert cfg.translator.all_settings["sarvam"]["api_key"] == "key1"
    finally:
        app_config.set_config_path(None)


# ---------------------------------------------------------------------------
# Sarvam Translator
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self):
        return self._payload


def test_sarvam_translator_needs_api_key():
    t = SarvamTranslator()
    assert not t.is_configured({})
    with pytest.raises(AdapterError) as exc:
        t.translate_batch(["hello"], "hi", {})
    assert exc.value.setup_required is True


def test_sarvam_translator_calls_endpoint_with_correct_body():
    t = SarvamTranslator()
    captured: dict = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["body"] = kw["json"]
        captured["headers"] = kw["headers"]
        return FakeResponse(200, {"translated_text": "नमस्ते"})

    with patch("prashnam_voice.adapters.sarvam.translator.requests.post", fake_post):
        out = t.translate_batch(["hello"], "hi", {"api_key": "k"})

    assert out == ["नमस्ते"]
    assert captured["url"] == "https://api.sarvam.ai/translate"
    assert captured["body"]["target_language_code"] == "hi-IN"
    assert captured["body"]["source_language_code"] == "en-IN"
    assert captured["body"]["input"] == "hello"
    assert captured["headers"]["api-subscription-key"] == "k"


def test_sarvam_translator_maps_odia_to_od_in():
    t = SarvamTranslator()
    captured: dict = {}

    def fake_post(url, **kw):
        captured["body"] = kw["json"]
        return FakeResponse(200, {"translated_text": "ଓଡ଼ିଆ"})

    with patch("prashnam_voice.adapters.sarvam.translator.requests.post", fake_post):
        t.translate_batch(["x"], "or", {"api_key": "k"})
    assert captured["body"]["target_language_code"] == "od-IN"


def test_sarvam_translator_401_marks_setup_required():
    t = SarvamTranslator()
    def fake_post(url, **kw): return FakeResponse(401, {}, text="forbidden")
    with patch("prashnam_voice.adapters.sarvam.translator.requests.post", fake_post):
        with pytest.raises(AdapterError) as exc:
            t.translate_batch(["x"], "hi", {"api_key": "wrong"})
    assert exc.value.setup_required is True


def test_sarvam_translator_unsupported_lang():
    t = SarvamTranslator()
    with pytest.raises(AdapterError):
        t.translate_batch(["x"], "zz", {"api_key": "k"})


def test_sarvam_translator_english_passthrough_no_http_call():
    """en→en should never hit the network."""
    t = SarvamTranslator()
    called = {"count": 0}

    def fake_post(*args, **kwargs):
        called["count"] += 1
        return FakeResponse(500, {})

    with patch("prashnam_voice.adapters.sarvam.translator.requests.post", fake_post):
        out = t.translate_batch(["press one"], "en", {"api_key": "k"})

    assert out == ["press one"]
    assert called["count"] == 0


def test_sarvam_tts_supports_english_lang_map():
    from prashnam_voice.adapters.sarvam._common import LANG_MAP
    assert LANG_MAP["en"] == "en-IN"


# ---------------------------------------------------------------------------
# Sarvam TTS
# ---------------------------------------------------------------------------


def test_sarvam_tts_returns_decoded_mp3():
    t = SarvamTTS()
    fake_audio_bytes = b"\xff\xfb\x90fake mp3 bytes"
    encoded = base64.b64encode(fake_audio_bytes).decode("ascii")

    def fake_post(url, **kw):
        return FakeResponse(200, {"request_id": "r1", "audios": [encoded]})

    with patch("prashnam_voice.adapters.sarvam.tts.requests.post", fake_post):
        out = t.synthesize("hello", "hi", "shubh", "moderate", {"api_key": "k"})
    assert out == fake_audio_bytes


def test_sarvam_tts_pace_mapping():
    t = SarvamTTS()
    captured: dict = {}

    def fake_post(url, **kw):
        captured["body"] = kw["json"]
        return FakeResponse(200, {"audios": [base64.b64encode(b"x").decode()]})

    with patch("prashnam_voice.adapters.sarvam.tts.requests.post", fake_post):
        t.synthesize("hi", "hi", "shubh", "very_slow", {"api_key": "k"})
    assert captured["body"]["pace"] < 1.0

    with patch("prashnam_voice.adapters.sarvam.tts.requests.post", fake_post):
        t.synthesize("hi", "hi", "shubh", "very_fast", {"api_key": "k"})
    assert captured["body"]["pace"] > 1.0


def test_sarvam_tts_falls_back_for_unknown_speaker():
    t = SarvamTTS()
    captured: dict = {}

    def fake_post(url, **kw):
        captured["body"] = kw["json"]
        return FakeResponse(200, {"audios": [base64.b64encode(b"x").decode()]})

    with patch("prashnam_voice.adapters.sarvam.tts.requests.post", fake_post):
        t.synthesize("hi", "hi", "Divya", "moderate", {"api_key": "k"})
    # Sarvam doesn't know "Divya" → should fall back to a known one.
    assert captured["body"]["speaker"] in [
        "shubh", "aditya", "aayan", "advait", "amit",  # any in V3_SPEAKERS
    ]


def test_sarvam_tts_voices_for_returns_v3_pool():
    t = SarvamTTS()
    voices = t.voices_for("hi", {"api_key": "k"})
    ids = {v.id for v in voices}
    assert "shubh" in ids
    assert len(voices) >= 30


def test_local_tts_voices_for_uses_per_language_pool():
    """The local Parler adapter should expose the model card's
    per-language speakers, not a single shared list."""
    from prashnam_voice.adapters.local.tts import LocalTTS
    t = LocalTTS()

    # Hindi pool: Rohit/Divya/Aman/Rani per the model card.
    hi = {v.id for v in t.voices_for("hi", {})}
    for expected in ("Rohit", "Divya", "Aman", "Rani"):
        assert expected in hi, f"Hindi voices missing {expected!r}: {hi}"

    # Tamil-specific pool: Kavitha + Jaya.
    ta = {v.id for v in t.voices_for("ta", {})}
    assert "Kavitha" in ta
    assert "Jaya" in ta

    # Languages without a card-listed pool fall back to a generic set.
    sat = [v.id for v in t.voices_for("sat", {})]
    assert len(sat) >= 5  # fallback pool is 10 strong
