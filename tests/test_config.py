import pytest

from prashnam_voice.config import ALL_LANG_CODES, LANGUAGES, parse_langs


def test_languages_present():
    # 10 Indic + English. English first so new-project defaults include it.
    expected = {"en", "hi", "ta", "te", "bn", "mr", "kn", "gu", "pa", "ml", "or"}
    assert set(ALL_LANG_CODES) == expected
    assert set(LANGUAGES.keys()) == expected
    assert ALL_LANG_CODES[0] == "en"


def test_each_language_has_voice_and_it2_tag():
    for code, spec in LANGUAGES.items():
        assert spec.code == code
        assert spec.it2.endswith("_" + spec.it2.split("_")[1])  # well-formed
        assert spec.voice, f"missing voice for {code}"


def test_parse_langs_all():
    assert parse_langs("all") == ALL_LANG_CODES
    assert parse_langs("") == ALL_LANG_CODES


def test_parse_langs_subset():
    assert parse_langs("hi,ta,bn") == ["hi", "ta", "bn"]


def test_parse_langs_dedupes_and_normalizes():
    assert parse_langs(" HI , hi , ta ") == ["hi", "ta"]


def test_parse_langs_rejects_unknown():
    with pytest.raises(ValueError):
        parse_langs("hi,xx")
