from prashnam_voice.text_normalize import numerals_to_words


def test_basic_integer():
    assert numerals_to_words("press 1") == "press one"


def test_year_2026():
    out = numerals_to_words("election 2026")
    # num2words may say "two thousand twenty-six" or "two thousand and
    # twenty-six" depending on locale defaults; just check the words.
    assert "two thousand" in out and "twenty-six" in out


def test_comma_separated_thousands():
    out = numerals_to_words("10,000 voters")
    assert "ten thousand" in out
    assert "10,000" not in out


def test_decimals():
    out = numerals_to_words("1.5 percent")
    assert "one point five" in out


def test_alphanumerics_left_alone():
    # "BJP123" shouldn't become "BJPone hundred twenty three"
    assert numerals_to_words("BJP123") == "BJP123"
    assert numerals_to_words("Q1 results") == "Q1 results"


def test_multiple_numbers_in_one_string():
    out = numerals_to_words("press 1 for option 2")
    assert "press one" in out
    assert "option two" in out
    assert "1" not in out
    assert "2" not in out


def test_empty_and_no_digits():
    assert numerals_to_words("") == ""
    assert numerals_to_words("hello world") == "hello world"


def test_effective_text_normalizes_option_index(tmp_path):
    from prashnam_voice.projects import (
        DEFAULT_OPTION_TEMPLATE,
        ProjectStore,
        effective_text,
    )
    store = ProjectStore(tmp_path)
    proj = store.create("E", ["hi"])
    store.add_segment(proj.id, "question", "Q?")
    o1 = store.add_segment(proj.id, "option", "Congress")

    proj = store.load(proj.id)
    seg = proj.find_segment(o1.id)
    text = effective_text(proj, seg)
    # Default template is "If you think {body}, then press {n}." → n=1
    # Numeral normalization should turn "1" into "one".
    assert "one" in text
    assert "press 1" not in text


def test_effective_text_normalizes_body_numerals(tmp_path):
    from prashnam_voice.projects import ProjectStore, effective_text
    store = ProjectStore(tmp_path)
    proj = store.create("Y", ["hi"])
    seg = store.add_segment(
        proj.id, "question", "Will the BJP win in 2026?"
    )
    proj = store.load(proj.id)
    text = effective_text(proj, proj.find_segment(seg.id))
    assert "two thousand" in text
    assert "2026" not in text
