from prashnam_voice.cache import _key, cache_path


def test_cache_key_changes_with_inputs():
    a = _key("hello", "hi", "Divya", "moderate")
    b = _key("hello", "ta", "Divya", "moderate")
    c = _key("hello", "hi", "Aditi", "moderate")
    d = _key("hi there", "hi", "Divya", "moderate")
    e = _key("hello", "hi", "Divya", "slow")
    assert len({a, b, c, d, e}) == 5


def test_cache_path_under_audio_cache_dir():
    p = cache_path("hello", "hi", "Divya", "moderate")
    assert p.suffix == ".mp3"
    assert p.parent.name == "audio"
