import json

from studio.script import validate_script
from studio.shorts import group_sections, plan_shorts, scene_durations

LONG = validate_script({
    "title": "Fix Your Google Listing",
    "thumbnail_text": "NO CALLS?",
    "tags": ["local seo"],
    "scenes": [
        {"section": "HOOK", "narration": "Hook words.", "on_screen_text": "Hook"},
        {"section": "STEP 1", "narration": "Step one part a.", "on_screen_text": "Add photos",
         "visual_query": "camera"},
        {"section": "STEP 1", "narration": "Step one part b.", "visual_query": "phone"},
        {"section": "STEP 2", "narration": "Too short.", "on_screen_text": "Hours"},
        {"section": "STEP 3", "narration": "Way too long.", "on_screen_text": "Reviews"},
        {"section": "CTA", "narration": "Call to action."},
    ],
}, "long", topic="google listing")

DURATIONS = [20.0, 12.0, 14.0, 8.0, 120.0, 20.0]


def test_group_sections_merges_consecutive_scenes():
    groups = group_sections(LONG["scenes"], DURATIONS)

    assert [(name, idx) for name, idx, _ in groups] == [
        ("HOOK", [0]), ("STEP 1", [1, 2]), ("STEP 2", [3]), ("STEP 3", [4]), ("CTA", [5])]
    assert groups[1][2] == 26.0


def test_plan_keeps_only_steps_of_usable_length():
    shorts = plan_shorts(LONG, DURATIONS, "Follow us.")

    assert len(shorts) == 1
    short = shorts[0]
    assert short["format"] == "short"
    assert short["title"] == "Add photos | Fix Your Google Listing"
    assert short["thumbnail_text"] == "Add photos"
    assert [s["narration"] for s in short["scenes"]] == [
        "Step one part a.", "Step one part b.", "Follow us."]
    assert short["scenes"][-1]["visual_query"] == "phone"
    assert "shorts" in short["tags"]


def test_plan_respects_max_and_does_not_mutate_source():
    before = json.dumps(LONG, sort_keys=True)
    durations = [20.0, 12.0, 14.0, 20.0, 30.0, 20.0]

    assert len(plan_shorts(LONG, durations, "Follow.", max_shorts=2)) == 2
    assert json.dumps(LONG, sort_keys=True) == before


def test_scene_durations_prefer_rendered_state(tmp_path):
    scene_dir = tmp_path / "build" / "scene_02"
    scene_dir.mkdir(parents=True)
    (scene_dir / "state.json").write_text(
        json.dumps({"narration": "Step one part a.", "duration": 9.5}), encoding="utf-8")

    durations = scene_durations(tmp_path, LONG)

    assert durations[1] == 9.5
    assert durations[0] == 2 / 150 * 60  # "Hook words." estimated from word count
