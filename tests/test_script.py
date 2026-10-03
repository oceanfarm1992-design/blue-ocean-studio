import pytest

from studio.config import load_config
from studio.script import (
    ScriptError,
    build_prompt,
    to_markdown,
    unchecked_scenes,
    validate_script,
    word_count,
)

RAW = {
    "title": "Get 50 Google Reviews in 30 Days",
    "title_options": ["A", "", "B"],
    "thumbnail_text": "50 REVIEWS",
    "description": "How to ask.",
    "tags": ["google reviews", "local seo"],
    "scenes": [
        {"section": "hook", "narration": "Most shops never ask.", "visual_query": "shop counter"},
        {"narration": "[CHECK: 7 in 10 customers leave a review when asked.]"},
    ],
}


def test_validate_cleans_and_fills_defaults():
    script = validate_script(RAW, "long", topic="reviews")

    assert script["format"] == "long"
    assert script["title_options"] == ["A", "B"]
    assert script["scenes"][0]["section"] == "HOOK"
    assert script["scenes"][1]["section"] == "MAIN"
    assert script["scenes"][1]["visual_query"] == "small business owner"
    assert script["scenes"][1]["on_screen_text"] == ""


def test_validate_does_not_mutate_input():
    before = repr(RAW)
    validate_script(RAW, "long")
    assert repr(RAW) == before


@pytest.mark.parametrize("bad, message", [
    ({"scenes": [{"narration": "x"}]}, "no title"),
    ({"title": "T", "scenes": []}, "no scenes"),
    ({"title": "T", "scenes": [{"narration": "  "}]}, "no narration"),
    ("not a dict", "JSON object"),
])
def test_validate_rejects_bad_scripts(bad, message):
    with pytest.raises(ScriptError, match=message):
        validate_script(bad, "long")


def test_validate_rejects_unknown_format():
    with pytest.raises(ScriptError, match="Unknown format"):
        validate_script(RAW, "reel")


def test_unchecked_scenes_finds_fact_check_markers():
    assert unchecked_scenes(validate_script(RAW, "long")) == [2]


def test_markdown_lists_scenes_and_word_count():
    script = validate_script(RAW, "short")
    md = to_markdown(script)

    assert md.startswith("# Get 50 Google Reviews in 30 Days")
    assert "### 01 · HOOK" in md
    assert f"{word_count(script)} words" in md


def test_prompts_include_channel_cta_and_format():
    cfg = load_config()
    _, long_user = build_prompt(cfg, "menu pricing", "long")
    _, short_user = build_prompt(cfg, "menu pricing", "short")

    assert cfg.channel["cta"] in long_user
    assert cfg.channel["follow_line"] in short_user
    assert "vertical short" in short_user
