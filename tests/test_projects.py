from pathlib import Path

import pytest

from prashnam_voice.projects import (
    DEFAULT_OPTION_TEMPLATE,
    DEFAULT_QUESTION_TEMPLATE,
    ProjectStore,
    SEGMENT_TYPES,
    effective_text,
)


def test_create_and_load(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("My Poll", ["hi", "ta"])
    assert proj.name == "My Poll"
    assert proj.langs == ["hi", "ta"]
    assert proj.id.startswith("my-poll-")

    again = store.load(proj.id)
    assert again.id == proj.id
    assert again.langs == ["hi", "ta"]


def test_create_defaults_to_en_plus_hi(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("New", langs=None)
    assert proj.langs == ["en", "hi"]


def test_create_filters_unknown_codes(tmp_path):
    store = ProjectStore(tmp_path)
    # Unknown codes are silently dropped; if everything's dropped, fall
    # back to the default project langs rather than failing.
    proj = store.create("X", langs=["xx", "yy"])
    assert proj.langs == ["en", "hi"]


def test_list_projects_orders_by_updated(tmp_path):
    store = ProjectStore(tmp_path)
    a = store.create("A", ["hi"])
    b = store.create("B", ["hi"])
    rows = store.list_projects()
    assert {r["id"] for r in rows} == {a.id, b.id}
    # a was created first, so b should be at the top (newer updated_at)
    assert rows[0]["id"] == b.id


def test_add_and_delete_segment(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("X", ["hi"])
    seg = store.add_segment(proj.id, "question", "Who will win?")
    assert seg.english == "Who will win?"

    # Persisted
    p = store.load(proj.id)
    assert len(p.segments) == 1
    assert p.segments[0].id == seg.id

    store.delete_segment(proj.id, seg.id)
    p2 = store.load(proj.id)
    assert p2.segments == []


def test_edit_english_clears_translations_and_takes(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("Y", ["hi", "ta"])
    seg = store.add_segment(proj.id, "question", "Hello?")

    # Plant a fake translation + current take.
    def _plant(p):
        s = p.find_segment(seg.id)
        s.translations = {"hi": {"r0": "नमस्ते?"}, "ta": {"r0": "வணக்கம்?"}}
        s.current_takes = {"hi": {"r0": "att_x"}, "ta": {"r0": "att_y"}}
    store.mutate(proj.id, _plant)

    new_seg, invalidated = store.edit_segment_english(proj.id, seg.id, "Goodbye?")
    assert sorted(invalidated) == ["hi", "ta"]
    assert new_seg.english == "Goodbye?"
    assert new_seg.translations == {}
    assert new_seg.current_takes == {}


def test_edit_english_no_change_is_noop(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("Z", ["hi"])
    seg = store.add_segment(proj.id, "question", "Same.")
    store.mutate(proj.id, lambda p: p.find_segment(seg.id).set_translation("hi", "r0", "वही."))
    new_seg, invalidated = store.edit_segment_english(proj.id, seg.id, "Same.")
    assert invalidated == []
    assert new_seg.translations == {"hi": {"r0": "वही."}}


def test_set_current_take_requires_existing_attempt(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("T", ["hi"])
    seg = store.add_segment(proj.id, "question", "Q")
    with pytest.raises(FileNotFoundError):
        store.set_current_take(proj.id, seg.id, "hi", "att_does_not_exist")


def test_update_settings_validates(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("S", ["hi"])
    store.update_settings(proj.id, default_pace="slow")
    assert store.load(proj.id).default_pace == "slow"

    with pytest.raises(ValueError):
        store.update_settings(proj.id, default_pace="warp_speed")
    with pytest.raises(ValueError):
        store.update_settings(proj.id, langs=["zz"])


def test_delete_project_removes_directory(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("D", ["hi"])
    assert (tmp_path / proj.id).is_dir()
    store.delete(proj.id)
    assert not (tmp_path / proj.id).exists()


def test_segment_types_constant():
    assert "question" in SEGMENT_TYPES and "option" in SEGMENT_TYPES


def test_default_templates_present_on_new_project(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("Defaults", ["hi"])
    assert proj.question_template == DEFAULT_QUESTION_TEMPLATE
    assert proj.option_template == DEFAULT_OPTION_TEMPLATE


def test_effective_text_wraps_question(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("E", ["hi"])
    seg = store.add_segment(proj.id, "question", "Who will win?")
    proj = store.load(proj.id)
    s = proj.find_segment(seg.id)
    text = effective_text(proj, s)
    assert text.startswith("Namaskar, this is a call from Prashnam")
    assert text.endswith("Who will win?")


def test_effective_text_wraps_option_with_index(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("E", ["hi"])
    store.add_segment(proj.id, "question", "Q?")
    o1 = store.add_segment(proj.id, "option", "Congress")
    o2 = store.add_segment(proj.id, "option", "BJP")
    proj = store.load(proj.id)
    e1 = effective_text(proj, proj.find_segment(o1.id))
    e2 = effective_text(proj, proj.find_segment(o2.id))
    # `effective_text` normalizes numerals to words so the TTS pronounces
    # them: {n}=1 → "one", {n}=2 → "two".
    assert "Congress" in e1 and e1.rstrip(".").endswith("press one")
    assert "BJP" in e2 and e2.rstrip(".").endswith("press two")


def test_effective_text_respects_use_template_flag(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("E", ["hi"])
    seg = store.add_segment(proj.id, "question", "Bare question")
    store.set_segment_use_template(proj.id, seg.id, False)
    proj = store.load(proj.id)
    s = proj.find_segment(seg.id)
    assert effective_text(proj, s) == "Bare question"


def test_toggle_use_template_clears_translations(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("E", ["hi"])
    seg = store.add_segment(proj.id, "question", "Q?")
    store.mutate(proj.id, lambda p: p.find_segment(seg.id).set_translation("hi", "r0", "x"))
    store.set_segment_use_template(proj.id, seg.id, False)
    s = store.load(proj.id).find_segment(seg.id)
    assert s.translations == {}


def test_lexicon_global_substitution(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("L", ["hi"])
    seg = store.add_segment(proj.id, "question", "Will the BJP win?")
    store.update_settings(proj.id, lexicon={"global": {"BJP": "bee jay pee"}})
    proj = store.load(proj.id)
    text = effective_text(proj, proj.find_segment(seg.id))
    assert "bee jay pee" in text
    assert "BJP" not in text


def test_lexicon_per_lang_overrides_global(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("L", ["hi", "ta"])
    seg = store.add_segment(proj.id, "question", "Will the BJP win?")
    store.update_settings(proj.id, lexicon={
        "global": {"BJP": "bee jay pee"},
        "hi": {"BJP": "बीजेपी"},
    })
    proj = store.load(proj.id)
    s = proj.find_segment(seg.id)

    hi_text = effective_text(proj, s, lang="hi")
    ta_text = effective_text(proj, s, lang="ta")
    assert "बीजेपी" in hi_text
    assert "bee jay pee" in ta_text   # falls back to global
    no_lang = effective_text(proj, s)
    assert "bee jay pee" in no_lang   # without lang, only global applies


def test_lexicon_substitution_is_whole_word(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("L", ["hi"])
    # "AAP" is a key; "AAPL" should NOT trigger a substitution.
    seg = store.add_segment(proj.id, "question", "AAPL Inc and AAP differ.")
    store.update_settings(proj.id, lexicon={"global": {"AAP": "ay ay pee"}})
    proj = store.load(proj.id)
    text = effective_text(proj, proj.find_segment(seg.id))
    assert "AAPL" in text
    assert "ay ay pee" in text


def test_lexicon_change_invalidates_translations(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("L", ["hi"])
    seg = store.add_segment(proj.id, "question", "Q?")
    store.mutate(proj.id, lambda p: p.find_segment(seg.id).set_translation("hi", "r0", "x"))
    store.update_settings(proj.id, lexicon={"global": {"foo": "bar"}})
    s = store.load(proj.id).find_segment(seg.id)
    assert s.translations == {}


def test_changing_question_template_invalidates_question_translations(tmp_path):
    store = ProjectStore(tmp_path)
    proj = store.create("E", ["hi"])
    q = store.add_segment(proj.id, "question", "Q?")
    o = store.add_segment(proj.id, "option", "A")
    store.mutate(proj.id, lambda p: (
        p.find_segment(q.id).set_translation("hi", "r0", "QH"),
        p.find_segment(o.id).set_translation("hi", "r0", "OH"),
    ))
    store.update_settings(proj.id, question_template="Hello {body}")
    p = store.load(proj.id)
    assert p.find_segment(q.id).translations == {}                       # cleared
    assert p.find_segment(o.id).translations == {"hi": {"r0": "OH"}}     # untouched
