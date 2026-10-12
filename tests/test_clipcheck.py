"""Picking stock clips that really fit the scene."""
from dataclasses import replace
from pathlib import Path

from studio import clipcheck, pipeline, visuals
from studio.config import load_config
from studio.llm import LLMError, _user_content
from studio.visuals import ClipFinder, match_score

CFG = load_config()
SIZE = (1080, 1920)


def _video(vid: int, title: str, image: str = "https://img/x.jpg") -> dict:
    return {"id": vid, "url": f"https://www.pexels.com/video/{title}-{vid}/", "image": image,
            "user": {"name": "Ana"}}


class OfflineFinder(ClipFinder):
    """Real ranking and checking; search and download replaced."""

    def __init__(self, tmp_path: Path, videos: list[dict], checker=None, strict: bool = True):
        super().__init__("key", tmp_path, SIZE, strict=strict, checker=checker)
        self.videos = videos
        self.fetched: list[int] = []

    def find(self, query: str, count: int) -> list[Path]:
        return self._pick(query, self._candidates(query, self.videos), count)

    def _fetch(self, video: dict) -> Path:
        self.fetched.append(video["id"])
        path = self.cache_dir / f"{video['id']}_1.mp4"
        self.used_ids.add(video["id"])
        credit = f"Video by Ana on Pexels: {video['url']}"
        self.credits.append(credit)
        self.credit_for[str(path)] = credit
        return path


def test_match_score_counts_shared_words():
    assert match_score("baker packing bag", _video(1, "packing-food-delivery")) == 1
    assert match_score("baker packing bag", _video(2, "baker-packing-pastries-into-bag")) == 3
    assert match_score("baker packing bag", _video(3, "family-at-the-beach")) == 0


def test_best_title_matches_are_tried_first(tmp_path):
    finder = OfflineFinder(tmp_path, [
        _video(1, "woman-packing-her-bag"),
        _video(2, "family-at-the-beach"),
        _video(3, "baker-packing-pastries-into-bag"),
    ])

    finder.find("baker packing bag", 2)

    assert finder.fetched == [3, 1]


def test_checker_rejects_unrelated_clips_without_padding(tmp_path):
    seen = []

    def checker(query, video):
        seen.append(video["id"])
        return video["id"] == 3

    finder = OfflineFinder(tmp_path, [_video(1, "woman-packing-her-bag"),
                                      _video(3, "baker-packing-bag")], checker)

    clips = finder.find("baker packing bag", 3)

    assert [p.name for p in clips] == ["3_1.mp4"]   # one good clip, not three mixed ones
    assert sorted(seen) == [1, 3]


def test_checker_calls_are_capped(tmp_path):
    calls = []
    videos = [_video(i, f"baker-number-{i}") for i in range(1, 20)]
    finder = OfflineFinder(tmp_path, videos, lambda q, v: calls.append(v) and False)

    assert finder.find("baker", 2) == []
    assert len(calls) == visuals.MAX_AI_CHECKS


def test_user_content_adds_low_detail_image():
    assert _user_content("hi", None) == "hi"
    parts = _user_content("hi", "https://img/x.jpg")
    assert parts[1]["image_url"] == {"url": "https://img/x.jpg", "detail": "low"}


def test_checker_off_without_key_or_setting():
    assert clipcheck.make_checker(replace(CFG, openai_key=None)) is None
    off = replace(CFG, openai_key="sk", video={**CFG.video, "check_stock_with_ai": False})
    assert clipcheck.make_checker(off) is None


def test_checker_sends_image_and_reads_answer(monkeypatch):
    sent = {}

    def fake(cfg, system, user, image_url=None, quiet=False):
        sent.update(user=user, image=image_url, quiet=quiet)
        return {"match": False}

    monkeypatch.setattr(clipcheck, "complete_json", fake)
    check = clipcheck.make_checker(replace(CFG, openai_key="sk"))

    assert check("baker waving", _video(9, "happy-family-waving")) is False
    assert sent["image"] == "https://img/x.jpg" and sent["quiet"]
    assert "happy-family-waving" in sent["user"]


def test_checker_keeps_clip_when_ai_unavailable(monkeypatch):
    def fail(*args, **kwargs):
        raise LLMError("rate limit")

    monkeypatch.setattr(clipcheck, "complete_json", fail)
    check = clipcheck.make_checker(replace(CFG, openai_key="sk"))

    assert check("baker", _video(1, "baker")) is True
    assert check("baker", _video(2, "baker", image="")) is True


def test_cached_stock_scene_keeps_its_credits(tmp_path):
    cfg = replace(CFG, openai_key=None)
    clip = tmp_path / "3_1.mp4"
    clip.write_bytes(b"")
    scene = {"section": "TIP", "narration": "n", "visual_query": "baker packing bag",
             "on_screen_text": "t", "visual": "stock", "doodle_prompt": ""}
    first = OfflineFinder(tmp_path, [_video(3, "baker-packing-bag")])
    state = pipeline._scene_clips(cfg, scene, tmp_path, {}, first, 3.0, SIZE, 3.5)

    again = OfflineFinder(tmp_path, [])
    pipeline._scene_clips(cfg, scene, tmp_path, state, again, 3.0, SIZE, 3.5)

    assert state["credits"] == ["Video by Ana on Pexels: https://www.pexels.com/video/baker-packing-bag-3/"]
    assert again.credits == state["credits"] and again.fetched == []
