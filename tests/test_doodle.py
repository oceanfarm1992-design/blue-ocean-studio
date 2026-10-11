import io
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio import doodle
from studio.config import load_config

CFG = load_config()


def _cfg(key: str | None = "sk-test", **doodle_settings):
    return replace(CFG, openai_key=key, doodle={**doodle.DEFAULTS, **doodle_settings})


def _white(height: int, width: int) -> np.ndarray:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_settings_fill_defaults_and_reject_unknown_mode():
    assert doodle.settings(replace(CFG, doodle=None))["mode"] == "mix"
    with pytest.raises(doodle.DoodleError):
        doodle.settings(_cfg(mode="sometimes"))


def test_wants_doodle_follows_mode_scene_and_key():
    scene = {"visual": "doodle", "visual_query": "q"}
    stock = {"visual": "stock", "visual_query": "q"}

    assert doodle.wants_doodle(_cfg(mode="mix"), scene)
    assert not doodle.wants_doodle(_cfg(mode="mix"), stock)
    assert doodle.wants_doodle(_cfg(mode="all"), stock)
    assert not doodle.wants_doodle(_cfg(mode="off"), scene)
    assert not doodle.wants_doodle(_cfg(key=None), scene)


def test_weak_stock_replacement_needs_setting_and_key():
    assert doodle.replaces_weak_stock(_cfg())
    assert not doodle.replaces_weak_stock(_cfg(replace_weak_stock=False))
    assert not doodle.replaces_weak_stock(_cfg(key=None))


def test_scene_prompt_falls_back_to_visual_query():
    assert doodle.scene_prompt({"doodle_prompt": "chair", "visual_query": "q"}) == "chair"
    assert doodle.scene_prompt({"doodle_prompt": "", "visual_query": "salon"}) == "salon"


def test_timing_enforces_minimum_hold_and_draw_cap():
    cfg = _cfg(min_scene_seconds=5.0, hold_seconds=1.0, max_draw_seconds=8.0)

    short = doodle.timing(cfg, 2.0)
    assert short.total == 6.0
    assert short.draw == pytest.approx(5.0 - doodle.EXIT_SECONDS)

    long = doodle.timing(cfg, 20.0)
    assert long.total == 21.0
    assert long.draw == 8.0


def test_timing_always_leaves_room_to_finish_drawing():
    t = doodle.timing(_cfg(min_scene_seconds=0.0, hold_seconds=0.0), 0.1)

    assert t.draw >= doodle.MIN_DRAW_SECONDS
    assert t.total >= t.draw + doodle.EXIT_SECONDS


def test_negative_pace_settings_rejected():
    with pytest.raises(doodle.DoodleError):
        doodle.settings(_cfg(hold_seconds=-1))


def test_fit_box_keeps_room_for_captions():
    w, h, x, y = doodle.fit_box((1024, 1536), (1080, 1920))
    assert (w, h) == (960, 1440)
    assert x == 60 and y + h < 1920 - 300

    w, h, x, y = doodle.fit_box((1536, 1024), (1920, 1080))
    assert h == 864 and x == (1920 - w) // 2


def test_landscape_drawing_is_centred_in_a_short():
    w, h, x, y = doodle.fit_box((1536, 1024), (1080, 1920))
    top = round(1920 * doodle.TOP_MARGIN)
    bottom = top + 1920 * doodle.BOX_HEIGHT_PORTRAIT

    assert w == round(1080 * doodle.BOX_WIDTH)
    assert abs((y - top) - (bottom - (y + h))) <= 1


def test_compose_snaps_off_white_background_to_white(tmp_path):
    path = tmp_path / "grey.png"
    Image.new("RGB", (40, 60), (250, 249, 248)).save(path)

    art = doodle.compose(path, (120, 200))

    assert (art == 255).all()


def test_stroke_order_visits_every_ink_cell_one_line_at_a_time():
    art = _white(56, 56)
    art[0:14, :] = 0      # top line: 4 cells
    art[42:56, 28:56] = 0  # bottom line: 2 cells

    order = doodle.stroke_order(art, cell=14)

    assert order[:4] == [(0, 0), (0, 1), (0, 2), (0, 3)]
    assert sorted(order[4:]) == [(3, 2), (3, 3)]


def test_stroke_order_empty_for_blank_page():
    assert doodle.stroke_order(_white(28, 28), cell=14) == []


def test_find_tip_is_upper_left_opaque_pixel():
    alpha = np.zeros((10, 10), dtype=np.uint8)
    alpha[3, 4] = alpha[8, 8] = alpha[5, 3] = 255

    assert doodle.find_tip(alpha) == (4, 3)
    with pytest.raises(doodle.DoodleError):
        doodle.find_tip(np.zeros((4, 4), dtype=np.uint8))


def test_bundled_hand_scales_with_frame():
    hand, tip = doodle.load_hand((1080, 1920))

    assert hand.width == round(1920 * doodle.HAND_SCALE)
    assert 0 <= tip[0] < hand.width / 2 and 0 <= tip[1] < hand.height / 2


def test_frames_draw_then_exit_then_hold():
    art = _white(28, 28)
    art[0:14, 0:14] = 0
    hand = Image.new("RGBA", (4, 4), (255, 0, 0, 255))

    out = list(doodle.frames(art, hand, (0, 0), total_frames=5, draw_frames=1, exit_frames=2))

    assert len(out) == 5
    assert out[-1] == art.tobytes()      # finished drawing, hand gone
    assert out[0] != art.tobytes()       # hand over the drawing while drawing


def test_doodle_image_reuses_any_cached_orientation(tmp_path, monkeypatch):
    cfg = _cfg()
    key = doodle._cache_key(doodle.settings(cfg), "chair")
    landscape = tmp_path / f"{key}_landscape.png"
    landscape.write_bytes(_png())
    monkeypatch.setattr(doodle, "_generate", lambda *a: pytest.fail("should use the cache"))

    assert doodle.doodle_image(cfg, "chair", (1080, 1920), tmp_path) == landscape


def test_doodle_image_generates_and_caches(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(doodle, "_generate", lambda cfg, s, prompt, size: calls.append(prompt) or _png())

    first = doodle.doodle_image(_cfg(), "chair", (1080, 1920), tmp_path)
    second = doodle.doodle_image(_cfg(), "chair", (1080, 1920), tmp_path)

    assert first == second and first.name.endswith("_portrait.png")
    assert len(calls) == 1 and doodle.STYLE in calls[0]


def test_corrupt_cache_is_redrawn_and_bad_api_data_rejected(tmp_path, monkeypatch):
    cfg = _cfg()
    key = doodle._cache_key(doodle.settings(cfg), "chair")
    broken = tmp_path / f"{key}_portrait.png"
    broken.write_bytes(b"half a png")
    monkeypatch.setattr(doodle, "_generate", lambda *a: _png())

    assert doodle.doodle_image(cfg, "chair", (1080, 1920), tmp_path) == broken
    assert doodle._is_image(broken.read_bytes())

    broken.unlink()
    monkeypatch.setattr(doodle, "_generate", lambda *a: b"<html>error</html>")
    with pytest.raises(doodle.DoodleError):
        doodle.doodle_image(cfg, "chair", (1080, 1920), tmp_path)
    assert not broken.exists()


def test_missing_hand_is_a_doodle_error(tmp_path):
    with pytest.raises(doodle.DoodleError):
        doodle.load_hand((1080, 1920), tmp_path / "nope.png")


def test_generate_without_key_raises():
    with pytest.raises(doodle.DoodleError):
        doodle._generate(_cfg(key=None), doodle.DEFAULTS, "chair", (1080, 1920))


def test_doodle_cmd_matches_scene_encoding():
    cmd = doodle.build_doodle_cmd(Path("voice.mp3"), (1080, 1920), 30, 6.5, "subs.ass",
                                  Path("scene.mp4"))
    joined = " ".join(cmd)

    assert "-f rawvideo -pix_fmt rgb24 -s 1080x1920 -r 30 -i -" in joined
    assert "subtitles=subs.ass" in joined and "setsar=1" in joined
    assert "out_color_matrix=bt709" in joined and "-colorspace bt709" in joined
    assert "apad=whole_dur=6.500" in joined
    assert "-c:v libx264" in joined and "-c:a aac" in joined
    assert cmd[-1] == "scene.mp4"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")
def test_render_scene_end_to_end(tmp_path):
    drawing = tmp_path / "drawing.png"
    Image.new("RGB", (64, 96), "white").save(drawing)
    with Image.open(drawing) as img:
        img.paste((0, 0, 0), (10, 10, 50, 20))
        img.save(drawing)
    audio = tmp_path / "voice.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=mono", "-t", "0.5", str(audio)], check=True)
    (tmp_path / "subs.ass").write_text("[Script Info]\nScriptType: v4.00+\n", encoding="utf-8")
    out = tmp_path / "scene.mp4"

    doodle.render_scene(drawing, audio, doodle.Timing(1.0, 0.4), (120, 200), 10, "subs.ass",
                        out, cwd=tmp_path)

    assert out.is_file() and out.stat().st_size > 0


def _probe(path: Path) -> list[str]:
    fields = "codec_name,width,height,pix_fmt,sample_aspect_ratio,r_frame_rate,color_space,sample_rate,channels"
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", f"stream={fields}",
                             "-of", "compact", str(path)], capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")
def test_doodle_and_stock_scenes_have_identical_streams(tmp_path):
    from studio.media import build_scene_cmd, run

    drawing = tmp_path / "drawing.png"
    Image.new("RGB", (64, 96), "white").save(drawing)
    audio = tmp_path / "voice.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", "0.5", str(audio)], check=True)
    (tmp_path / "subs.ass").write_text("[Script Info]\nScriptType: v4.00+\n", encoding="utf-8")
    doodle_out, stock_out = tmp_path / "doodle.mp4", tmp_path / "stock.mp4"

    doodle.render_scene(drawing, audio, doodle.Timing(1.0, 0.4), (120, 200), 10, "subs.ass",
                        doodle_out, cwd=tmp_path)
    run(build_scene_cmd([drawing], audio, 1.0, (120, 200), 10, "subs.ass", stock_out,
                        white_in=0.3), cwd=tmp_path)

    assert _probe(doodle_out) == _probe(stock_out)


def test_ffmpeg_is_stopped_when_frames_fail():
    class FakeProc:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.killed = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    def broken_frames():
        yield b"frame"
        raise ValueError("bad frame")

    proc = FakeProc()
    with pytest.raises(ValueError):
        doodle._feed(proc, broken_frames())
    assert proc.killed and proc.stdin.closed
