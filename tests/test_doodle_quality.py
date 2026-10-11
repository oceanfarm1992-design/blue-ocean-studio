"""Cheap low-quality drawings made crisp in Python, and reviewed before use."""
import io
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from studio import doodle, doodle_review
from studio.config import load_config
from studio.doodle_enhance import ACCENT, enhance
from studio.llm import LLMError

CFG = load_config()


def _cfg(**doodle_settings):
    return replace(CFG, openai_key="sk-test", doodle={**doodle.DEFAULTS, **doodle_settings})


def _soft_drawing() -> Image.Image:
    """What low quality looks like: blurred lines, grey haze, a speck, an off-blue fill."""
    image = Image.new("RGB", (200, 200), (246, 246, 244))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 180, 180), outline=(30, 30, 30), width=8)
    draw.rectangle((60, 60, 120, 120), fill=(40, 170, 170))      # teal-ish accent
    draw.ellipse((140, 140, 170, 170), fill=(200, 200, 200))      # grey smudge
    draw.point((100, 160), fill=(0, 0, 0))                         # single-pixel speck
    return image.filter(ImageFilter.GaussianBlur(1.5))


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_enhance_keeps_size_and_makes_paper_pure_white():
    out = np.asarray(enhance(_soft_drawing()))

    assert out.shape == (200, 200, 3)
    assert (out[5, 5] == 255).all()        # off-white paper becomes white
    assert (out[155, 155] == 255).all()    # grey smudge removed
    assert (out[160, 100] == 255).all()    # speck removed


def test_enhance_makes_lines_solid_and_accent_consistent():
    out = np.asarray(enhance(_soft_drawing())).astype(int)

    assert out[100, 22].max() < 60                            # line centre is solid ink
    assert np.abs(out[90, 90] - ACCENT.astype(int)).max() < 8  # fill repainted in brand blue


def test_enhance_never_turns_dark_blue_into_ink():
    image = Image.new("RGB", (120, 120), "white")
    ImageDraw.Draw(image).ellipse((30, 30, 90, 90), fill=(20, 60, 150))

    centre = np.asarray(enhance(image))[60, 60].astype(int)

    assert np.abs(centre - ACCENT.astype(int)).max() < 8


def test_review_reads_answer_and_sends_high_detail_preview(monkeypatch):
    sent = {}

    def fake(cfg, system, user, image_url=None, quiet=False, detail="low"):
        sent.update(url=image_url, detail=detail)
        return {"ok": False, "problem": "numbers on the board"}

    monkeypatch.setattr(doodle_review, "complete_json", fake)

    assert doodle_review.review(_cfg(), _soft_drawing(), "baker") == (False, "numbers on the board")
    assert sent["url"].startswith("data:image/jpeg;base64,") and sent["detail"] == "high"


def test_review_failure_accepts_drawing(monkeypatch):
    def fail(*args, **kwargs):
        raise LLMError("quota")

    monkeypatch.setattr(doodle_review, "complete_json", fail)

    assert doodle_review.review(_cfg(), _soft_drawing(), "baker")[0] is True


@pytest.fixture
def drawings(monkeypatch):
    """Counts generations; the review rejects whatever is listed in `rejected`."""
    calls = {"generated": 0, "reviewed": 0, "rejected": []}

    def generate(cfg, s, prompt, size):
        calls["generated"] += 1
        return _png(_soft_drawing())

    def review(cfg, image, prompt):
        calls["reviewed"] += 1
        bad = calls["reviewed"] in calls["rejected"]
        return (not bad, "writing" if bad else "")

    monkeypatch.setattr(doodle, "_generate", generate)
    monkeypatch.setattr(doodle, "review", review)
    return calls


def test_rejected_drawing_is_redrawn_once(tmp_path, drawings):
    drawings["rejected"] = [1]

    path = doodle.doodle_image(_cfg(), "baker", (1080, 1920), tmp_path)

    assert drawings["generated"] == 2 and drawings["reviewed"] == 1
    assert path.is_file()


def test_last_attempt_is_used_even_if_imperfect(tmp_path, drawings):
    drawings["rejected"] = [1, 2]

    doodle.doodle_image(_cfg(max_attempts=2), "baker", (1080, 1920), tmp_path)

    assert drawings["generated"] == 2   # never a third paid drawing


def test_good_drawing_is_drawn_once_and_saved_enhanced(tmp_path, drawings):
    path = doodle.doodle_image(_cfg(), "baker", (1080, 1920), tmp_path)

    assert drawings["generated"] == 1
    assert np.asarray(Image.open(path))[5, 5].tolist() == [255, 255, 255]


def test_enhance_and_review_can_be_switched_off(tmp_path, drawings):
    path = doodle.doodle_image(_cfg(enhance=False, review=False), "baker", (1080, 1920), tmp_path)

    assert drawings["reviewed"] == 0
    assert np.asarray(Image.open(path))[5, 5].tolist() != [255, 255, 255]


def test_enhance_setting_is_part_of_the_cache_key():
    s = doodle.settings(_cfg())
    assert doodle._cache_key(s, "baker") != doodle._cache_key({**s, "enhance": False}, "baker")
    assert doodle.settings(replace(CFG, doodle=None))["quality"] == "low"


def test_crop_trims_empty_paper_and_drawing_fills_frame(tmp_path):
    image = Image.new("RGB", (1000, 1500), "white")
    ImageDraw.Draw(image).rectangle((400, 600, 600, 760), outline=(0, 0, 0), width=6)
    path = tmp_path / "small.png"
    image.save(path)

    cropped = doodle.crop_to_drawing(image)
    art = doodle.compose(path, (1080, 1920))
    ys, xs = np.nonzero(art.min(axis=2) < 128)

    assert cropped.size == (201 + 2 * 60, 161 + 2 * 60)
    width = xs.max() - xs.min() + 1
    assert width == pytest.approx(201 * doodle.MAX_UPSCALE, abs=4)  # enlarged, but capped
    assert doodle.crop_to_drawing(Image.new("RGB", (50, 50), "white")).size == (50, 50)
