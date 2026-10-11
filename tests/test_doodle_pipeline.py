"""Choosing doodle or stock visuals per scene, and the voice fallback."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from studio import doodle, pipeline, voice
from studio.config import load_config
from studio.media import build_scene_cmd
from studio.script import to_markdown, validate_script
from studio.shorts import plan_shorts
from studio.visuals import ClipFinder, is_relevant

CFG = load_config()
SIZE = (1080, 1920)


def _cfg(key: str | None = "sk-test", **doodle_settings):
    return replace(CFG, openai_key=key, doodle={**doodle.DEFAULTS, **doodle_settings})


def _scene(visual: str = "stock", prompt: str = "") -> dict:
    return {"section": "TIP", "narration": "Try this.", "visual_query": "stylist blow drying",
            "on_screen_text": "Tip", "visual": visual, "doodle_prompt": prompt}


class FakeFinder(ClipFinder):
    def __init__(self, tmp_path: Path, clips: list[Path]):
        super().__init__("key", tmp_path, SIZE, strict=True)
        self.clips = clips
        self.queries: list[str] = []

    def find(self, query: str, count: int) -> list[Path]:
        self.queries.append(query)
        return self.clips


@pytest.fixture(autouse=True)
def edge_not_blocked(monkeypatch):
    monkeypatch.setattr(voice, "_edge_blocked", False)


@pytest.fixture
def drawing(tmp_path, monkeypatch):
    path = tmp_path / "drawing.png"
    path.write_bytes(b"png")
    monkeypatch.setattr(doodle, "doodle_image", lambda cfg, prompt, size, cache: path)
    return path


def test_doodle_scene_uses_drawing_and_skips_stock(tmp_path, drawing):
    finder = FakeFinder(tmp_path, [tmp_path / "1_a.mp4"])

    state = pipeline._scene_clips(_cfg(), _scene("doodle", "chair"), tmp_path, {}, finder,
                                  3.0, SIZE, 3.5)

    assert state["kind"] == "doodle" and state["clips"] == [str(drawing)]
    assert finder.queries == []


def test_stock_scene_keeps_footage(tmp_path, drawing):
    clip = tmp_path / "42_7.mp4"
    state = pipeline._scene_clips(_cfg(), _scene(), tmp_path, {}, FakeFinder(tmp_path, [clip]),
                                  3.0, SIZE, 3.5)

    assert state["kind"] == "stock" and state["clip_ids"] == [42]


def test_no_matching_footage_becomes_doodle(tmp_path, drawing):
    state = pipeline._scene_clips(_cfg(), _scene(), tmp_path, {}, FakeFinder(tmp_path, []),
                                  3.0, SIZE, 3.5)

    assert state["kind"] == "doodle"


def test_no_footage_and_doodles_off_becomes_card(tmp_path):
    state = pipeline._scene_clips(_cfg(mode="off"), _scene("doodle"), tmp_path, {},
                                  FakeFinder(tmp_path, []), 3.0, SIZE, 3.5)

    assert state["kind"] == "stock" and state["clips"][0].endswith("card.png")


def test_failed_drawing_falls_back_to_stock(tmp_path, monkeypatch):
    def fail(*args):
        raise doodle.DoodleError("quota")
    monkeypatch.setattr(doodle, "doodle_image", fail)
    clip = tmp_path / "5_1.mp4"

    state = pipeline._scene_clips(_cfg(), _scene("doodle"), tmp_path, {},
                                  FakeFinder(tmp_path, [clip]), 3.0, SIZE, 3.5)

    assert state["kind"] == "stock" and state["clips"] == [str(clip)]
    assert state["doodle_failed"]


def test_failed_doodle_is_retried_on_next_render(tmp_path, drawing):
    finder = FakeFinder(tmp_path, [tmp_path / "5_1.mp4"])
    clip = tmp_path / "5_1.mp4"
    clip.write_bytes(b"")
    scene = _scene("doodle")
    state = {"visual_key": pipeline._visual_key(_cfg(), scene, finder), "kind": "stock",
             "doodle_failed": True, "clips": [str(clip)], "clip_ids": [5]}

    state = pipeline._scene_clips(_cfg(), scene, tmp_path, state, finder, 3.0, SIZE, 3.5)

    assert state["kind"] == "doodle"


def test_quality_change_invalidates_visual_cache(tmp_path):
    finder = FakeFinder(tmp_path, [])
    assert pipeline._visual_key(_cfg(quality="low"), _scene(), finder) != \
        pipeline._visual_key(_cfg(quality="medium"), _scene(), finder)


def test_doodle_scene_assets_get_doodle_timing(tmp_path, drawing, monkeypatch):
    monkeypatch.setattr(pipeline, "_scene_voice", lambda cfg, scene, d, state: {
        **state, "duration": 2.0, "words": [["Try", 0.0, 0.4]]})
    cfg = _cfg(min_scene_seconds=5.0, hold_seconds=1.0)

    assets = pipeline._prepare_scene(cfg, _scene("doodle"), tmp_path / "s1",
                                     FakeFinder(tmp_path, []), SIZE, 3.5)

    assert assets.kind == "doodle"
    assert assets.duration == 6.0 and assets.draw_seconds > 0
    state = json.loads((tmp_path / "s1" / "state.json").read_text(encoding="utf-8"))
    assert state["scene_seconds"] == 6.0


def test_short_planning_uses_padded_scene_length(tmp_path):
    from studio.shorts import scene_durations

    scene_dir = tmp_path / "build" / "scene_01"
    scene_dir.mkdir(parents=True)
    (scene_dir / "state.json").write_text(json.dumps(
        {"narration": "Try this.", "duration": 2.0, "scene_seconds": 6.2}), encoding="utf-8")

    assert scene_durations(tmp_path, {"scenes": [_scene()]}) == [6.2]


def test_doodle_animation_failure_shows_still_drawing(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise pipeline.MediaError("ffmpeg crashed")
    commands = []
    monkeypatch.setattr(doodle, "render_scene", fail)
    monkeypatch.setattr(pipeline, "run", lambda cmd, cwd=None: commands.append(cmd))
    image = tmp_path / "drawing.png"
    assets = pipeline.SceneAssets(tmp_path / "v.mp3", 6.0, (), (image,), "doodle", 4.0)

    out = pipeline._render_scene(_cfg(), assets, tmp_path, SIZE, "short")

    assert out == tmp_path / "scene.mp4"
    assert str(image) in commands[0]


def test_voice_switch_mid_run_revoices_earlier_scenes(tmp_path, monkeypatch):
    calls = []

    def fake_synthesize(cfg, text, out):
        if len(calls) == 1:          # second scene: edge gets blocked
            monkeypatch.setattr(voice, "_edge_blocked", True)
        calls.append(voice.voice_id(cfg))
        out.write_bytes(b"audio")
        return voice.VoiceResult(out, 1.0, ())

    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = replace(_cfg(mode="off"), voice={**CFG.voice, "provider": "edge"})
    script = {"scenes": [_scene(), _scene()]}
    dirs = [tmp_path / "s1", tmp_path / "s2"]

    assets = pipeline._prepare_all(cfg, script, dirs, FakeFinder(tmp_path, []), SIZE, 3.5)

    assert len(assets) == 2
    states = [json.loads((d / "state.json").read_text(encoding="utf-8")) for d in dirs]
    assert all(s["voice"].startswith("openai:") for s in states)
    assert calls[-1].startswith("openai:") and len(calls) == 3


def test_doodle_captions_sit_lower_on_shorts():
    assert pipeline._caption_style("short", "doodle").margin_v < \
        pipeline._caption_style("short", "stock").margin_v
    assert pipeline._caption_style("long", "doodle") == pipeline.LONG_STYLE


def test_white_fade_only_when_requested():
    plain = " ".join(build_scene_cmd([Path("a.mp4")], Path("v.mp3"), 4.0, SIZE, 30, "s.ass",
                                     Path("o.mp4")))
    faded = " ".join(build_scene_cmd([Path("a.mp4")], Path("v.mp3"), 4.0, SIZE, 30, "s.ass",
                                     Path("o.mp4"), white_in=0.3))

    assert "fade=" not in plain
    assert "fade=t=in:st=0:d=0.30:color=white" in faded


@pytest.mark.parametrize("url, query, expected", [
    ("https://www.pexels.com/video/daily-monitoring-of-glucose-blood-level-6823847/",
     "salon owner checking calendar", False),
    ("https://www.pexels.com/video/hand-holding-pencil-pointing-at-calendar-7356018/",
     "salon owner checking calendar", True),
    ("https://www.pexels.com/video/team-filmmaking-workshop-37903875/",
     "woman filming salon reel", True),
    ("https://www.pexels.com/video/woman-cooking-pasta-123/", "woman reading phone reviews", False),
    ("https://www.pexels.com/video/8371258/", "anything", True),
    ("https://www.pexels.com/video/woman-cooking-pasta-123/", "young woman using", True),
])
def test_is_relevant_uses_pexels_title(url, query, expected):
    assert is_relevant(query, {"url": url}) is expected


def test_script_visual_fields_validated():
    script = validate_script({"title": "T", "scenes": [
        {"narration": "a", "visual": "DOODLE", "doodle_prompt": " chair "},
        {"narration": "b", "visual": "cartoon"},
        {"narration": "c"},
    ]}, "short")
    visuals = [(s["visual"], s["doodle_prompt"]) for s in script["scenes"]]

    assert visuals == [("doodle", "chair"), ("stock", ""), ("stock", "")]
    assert "*Doodle:* chair" in to_markdown(script)


def test_prompt_asks_for_visuals_only_in_mix_mode():
    from studio.script import build_prompt

    _, mixed = build_prompt(_cfg(mode="mix"), "salons", "short")
    _, off = build_prompt(_cfg(mode="off"), "salons", "short")

    assert '"visual": "stock or doodle"' in mixed and "doodle_prompt rules" in mixed
    assert "doodle" not in off


def test_cut_shorts_keep_scene_visuals_and_follow_with_stock():
    long = validate_script({"title": "Long", "scenes": [
        {"section": "STEP 1", "narration": "word " * 40, "visual": "doodle", "doodle_prompt": "p"},
    ]}, "long")

    shorts = plan_shorts(long, [20.0], "Follow us.")

    assert shorts[0]["scenes"][0]["visual"] == "doodle"
    assert shorts[0]["scenes"][-1]["visual"] == "stock"


def test_edge_failure_falls_back_to_openai(monkeypatch, tmp_path):
    def edge_fails(cfg, text, out):
        raise voice.VoiceError("Edge voice failed: 403")
    monkeypatch.setattr(voice, "_edge", edge_fails)
    monkeypatch.setattr(voice, "_openai", lambda cfg, text, out: ["openai"])
    cfg = replace(CFG, openai_key="sk-test")

    assert voice._edge_or_openai(cfg, "hi", tmp_path / "v.mp3") == ["openai"]
    with pytest.raises(voice.VoiceError):
        voice._edge_or_openai(replace(cfg, openai_key=None), "hi", tmp_path / "v.mp3")
