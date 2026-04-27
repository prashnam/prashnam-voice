import pytest

from prashnam_voice.config import ALL_LANG_CODES, LANGUAGES, parse_langs


def test_languages_present():
    # English + 22 Indic where IndicTrans2 + Indic Parler-TTS overlap.
    expected = {
        "en", "hi", "ta", "te", "bn", "mr", "kn", "gu", "ml", "or", "pa",
        "as", "ur", "ne", "sa", "mai", "ks", "sd", "brx", "doi", "kok", "mni", "sat",
    }
    assert set(ALL_LANG_CODES) == expected
    assert set(LANGUAGES.keys()) == expected
    # English first so new-project default surfaces it on top of the list.
    assert ALL_LANG_CODES[0] == "en"
    assert ALL_LANG_CODES[1] == "hi"


def test_default_project_langs():
    from prashnam_voice.config import DEFAULT_PROJECT_LANGS
    assert DEFAULT_PROJECT_LANGS == ["en", "hi"]


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
