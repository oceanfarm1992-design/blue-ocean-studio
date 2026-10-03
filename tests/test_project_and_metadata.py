from datetime import date

from studio.ideas import clean_ideas, ideas_to_markdown
from studio.metadata import build_chapters, build_metadata, format_timestamp
from studio.project import load_script, new_project_dir, save_script, slugify
from studio.script import validate_script

SCRIPT = validate_script(
    {"title": "Fix Your Menu", "scenes": [{"narration": "Hello there."}], "tags": ["menu"]},
    "short",
)


def test_slugify():
    assert slugify("Why Your Google Listing Gets No Calls!") == "why-your-google-listing-gets-no-calls"
    assert slugify("???") == "video"
    assert len(slugify("word " * 40)) <= 50


def test_new_project_dir_avoids_collisions(tmp_path):
    first = new_project_dir(tmp_path, "Fix Your Menu", "short", today=date(2026, 10, 3))
    assert first.name == "20261003-short-fix-your-menu"
    first.mkdir()
    second = new_project_dir(tmp_path, "Fix Your Menu", "short", today=date(2026, 10, 3))
    assert second.name == "20261003-short-fix-your-menu-2"


def test_save_and_load_round_trip(tmp_path):
    save_script(tmp_path, SCRIPT)

    assert load_script(tmp_path) == SCRIPT
    assert (tmp_path / "script.md").is_file()


def test_format_timestamp():
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(75.9) == "1:15"
    assert format_timestamp(3661) == "1:01:01"


def test_chapters_merge_sections_and_start_at_zero():
    sections = [("HOOK", 12), ("STEP 1", 20), ("STEP 1", 15), ("CTA", 11)]
    assert build_chapters(sections) == ["0:00 Hook", "0:12 Step 1", "0:47 Cta"]


def test_chapters_dropped_when_youtube_would_reject_them():
    assert build_chapters([("HOOK", 30), ("CTA", 30)]) == []
    assert build_chapters([("HOOK", 30), ("A", 5), ("CTA", 30)]) == []


def test_metadata_includes_cta_chapters_and_unique_credits():
    text = build_metadata(SCRIPT, "Free audit!", ["0:00 Hook"], ["Video by A", "Video by A"])

    assert "Free audit!" in text
    assert "Chapters:\n0:00 Hook" in text
    assert text.count("Video by A") == 1
    assert "menu" in text


def test_clean_ideas_skips_bad_items_and_fixes_format():
    ideas = clean_ideas({"ideas": [{"title": "A", "format": "SHORT"}, {"title": ""},
                                   "junk", {"title": "B", "format": "reel"}]})

    assert [(i["title"], i["format"]) for i in ideas] == [("A", "short"), ("B", "long")]
    assert "| 1 | short |" in ideas_to_markdown(ideas)
    assert clean_ideas(None) == []
