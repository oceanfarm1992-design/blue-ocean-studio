from studio.captions import (
    LONG_STYLE,
    SHORT_STYLE,
    Word,
    chunk_words,
    escape_ass_text,
    estimate_word_timings,
    format_ass_time,
    restore_punctuation,
    to_ass,
)


def test_estimate_spreads_words_across_full_duration():
    words = estimate_word_timings("one two three", 3.0)

    assert [w.text for w in words] == ["one", "two", "three"]
    assert words[0].start == 0
    assert abs(words[-1].end - 3.0) < 0.01
    assert all(a.end <= b.start + 0.001 for a, b in zip(words, words[1:]))


def test_estimate_returns_nothing_for_empty_text_or_zero_duration():
    assert estimate_word_timings("", 2.0) == ()
    assert estimate_word_timings("hello", 0) == ()


def test_restore_punctuation_uses_script_spelling():
    spoken = (Word("Hello", 0, 0.3), Word("world", 0.3, 0.6), Word("its", 0.7, 0.9))
    restored = restore_punctuation(spoken, "Hello, world. It's")

    assert [w.text for w in restored] == ["Hello,", "world.", "It's"]
    assert restored[1].start == 0.3


def test_restore_punctuation_keeps_unmatched_words():
    spoken = (Word("forty", 0, 0.3), Word("dollars", 0.3, 0.6))
    restored = restore_punctuation(spoken, "$40 order")

    assert [w.text for w in restored] == ["forty", "dollars"]


def test_chunk_breaks_on_max_words_pause_and_punctuation():
    words = (
        Word("a", 0.0, 0.1), Word("b", 0.1, 0.2), Word("c", 0.2, 0.3),
        Word("d", 1.0, 1.1),          # long pause before this word
        Word("e.", 1.1, 1.2), Word("f", 1.2, 1.3),
    )
    chunks = chunk_words(words, max_words=2)

    assert [[w.text for w in c] for c in chunks] == [["a", "b"], ["c"], ["d", "e."], ["f"]]


def test_format_ass_time():
    assert format_ass_time(0) == "0:00:00.00"
    assert format_ass_time(61.234) == "0:01:01.23"
    assert format_ass_time(3725.5) == "1:02:05.50"


def test_escape_removes_override_braces():
    assert escape_ass_text("{\\b1}bold\nnext") == "(b1)bold next"


def test_to_ass_contains_resolution_and_dialogue_lines():
    words = estimate_word_timings("Fix your Google listing today.", 2.0)
    ass = to_ass(words, 1920, 1080, "Segoe UI", LONG_STYLE)

    assert "PlayResX: 1920" in ass
    assert "Style: Default,Segoe UI,58" in ass
    assert ass.count("Dialogue:") == 1
    assert "Fix your Google listing today." in ass


def test_to_ass_uppercases_for_shorts():
    words = estimate_word_timings("big text here", 1.0)
    ass = to_ass(words, 1080, 1920, "Segoe UI", SHORT_STYLE)

    assert "BIG TEXT HERE" in ass
