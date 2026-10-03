from pathlib import Path

import pytest

from studio.config import load_config, read_env_file
from studio.media import MediaError, build_scene_cmd
from studio.visuals import ClipFinder, pick_video_file

FILES = [
    {"id": 1, "file_type": "video/mp4", "width": 3840, "height": 2160, "link": "4k"},
    {"id": 2, "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "hd"},
    {"id": 3, "file_type": "video/mp4", "width": 960, "height": 540, "link": "sd"},
    {"id": 4, "file_type": "video/webm", "width": 1280, "height": 720, "link": "webm"},
]


def test_pick_smallest_sharp_enough_mp4():
    assert pick_video_file(FILES, 1920, 1080)["link"] == "hd"


def test_pick_largest_when_none_sharp_enough():
    small = [f for f in FILES if f["id"] == 3]
    assert pick_video_file(small, 1920, 1080)["link"] == "sd"


def test_pick_none_without_mp4():
    assert pick_video_file([FILES[3]], 1920, 1080) is None


def test_clip_finder_disabled_without_key(tmp_path):
    finder = ClipFinder(None, tmp_path, (1080, 1920))

    assert not finder.enabled
    assert finder.orientation == "portrait"
    assert finder.find("cafe", 3) == []


def test_scene_cmd_mixes_video_and_image_inputs():
    cmd = build_scene_cmd([Path("a.mp4"), Path("card.png")], Path("voice.mp3"), 10.0,
                          (1920, 1080), 30, "subs.ass", Path("scene.mp4"))
    joined = " ".join(cmd)

    assert cmd[:2] == ["ffmpeg", "-y"]
    assert "-stream_loop -1 -i a.mp4" in joined
    assert "-loop 1 -framerate 30 -i card.png" in joined
    assert "trim=duration=5.000" in joined
    assert "concat=n=2" in joined
    assert "subtitles=subs.ass" in joined
    assert "[2:a]apad=whole_dur=10.000" in joined
    assert cmd[-1] == "scene.mp4"


def test_scene_cmd_requires_a_clip():
    with pytest.raises(MediaError):
        build_scene_cmd([], Path("v.mp3"), 1.0, (1920, 1080), 30, "s.ass", Path("o.mp4"))


def test_read_env_file_parses_comments_and_quotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\nA=1\nB="two"\nbroken line\n', encoding="utf-8")

    assert read_env_file(env) == {"A": "1", "B": "two"}
    assert read_env_file(tmp_path / "missing") == {}


def test_load_config_reads_legacy_key_name(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_AI_KEY", raising=False)
    real = load_config()
    (tmp_path / "channel.toml").write_text(
        (real.root / "channel.toml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / ".api.txt").write_text("OPEN_AI_KEY=test-key\n", encoding="utf-8")

    cfg = load_config(tmp_path)

    assert cfg.openai_key == "test-key"
    assert cfg.channel["name"] == "Blue Ocean Marketing"
