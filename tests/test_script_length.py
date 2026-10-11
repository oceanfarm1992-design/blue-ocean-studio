"""Long scripts must be long enough for an 8-minute video; the cloned voice skips RVC."""
import sys
import types
from dataclasses import replace

import pytest

from studio import script as script_module
from studio import voice
from studio.config import load_config
from studio.llm import LLMError
from studio.script import generate_script, min_long_words, word_count

CFG = load_config()


def _draft(words: int) -> dict:
    return {"title": "T", "scenes": [{"narration": " ".join(["word"] * words)}]}


def _fake_llm(monkeypatch, lengths: list[int]) -> list[str]:
    requests = []

    def fake(cfg, system, user):
        requests.append(user)
        answer = lengths[len(requests) - 1]
        if isinstance(answer, Exception):
            raise answer
        return _draft(answer)

    monkeypatch.setattr(script_module, "complete_json", fake)
    return requests


def test_prompt_asks_for_enough_scenes_not_just_words():
    _, user = script_module.build_prompt(CFG, "reviews", "long")
    scenes = script_module.min_long_scenes(CFG)

    low, _ = script_module.LONG_SCENE_WORDS

    assert f"At least {scenes} scenes" in user
    assert scenes * low >= min_long_words(CFG)    # even the shortest allowed scenes suffice
    assert min_long_words(CFG) >= CFG.video["long_minutes"] * 151   # measured pace on GitHub
    assert "HOOK (1 scene, under 40 words" in user


def test_doodle_rules_avoid_objects_with_writing():
    cfg = replace(CFG, doodle={**(CFG.doodle or {}), "mode": "mix"})
    _, user = script_module.build_prompt(cfg, "menus", "short")

    assert "menus, receipts, price tags" in user


def test_short_long_video_is_warned_about(capsys):
    from studio import pipeline

    assets = [pipeline.SceneAssets(None, 200.0, (), ()), pipeline.SceneAssets(None, 230.0, (), ())]
    pipeline._warn_if_short(CFG, {"format": "long"}, assets)
    pipeline._warn_if_short(CFG, {"format": "short"}, assets)

    assert capsys.readouterr().out.count("WARNING: long video is 7.2 min") == 1


def test_short_long_script_is_sent_back_with_its_word_count(monkeypatch):
    enough = min_long_words(CFG)
    requests = _fake_llm(monkeypatch, [600, enough])

    script = generate_script(CFG, "reviews", "long")

    assert word_count(script) == enough
    assert len(requests) == 2 and "600 spoken words" in requests[1]
    assert f"at least {script_module.min_long_scenes(CFG)} scenes" in requests[1]


def test_longest_draft_used_when_every_attempt_is_short(monkeypatch, capsys):
    requests = _fake_llm(monkeypatch, [500, 900, 700])

    script = generate_script(CFG, "reviews", "long")

    assert word_count(script) == 900 and len(requests) == script_module.LONG_ATTEMPTS
    assert "WARNING" in capsys.readouterr().out


def test_errors_between_short_drafts_keep_the_longest(monkeypatch, capsys):
    requests = _fake_llm(monkeypatch, [600, LLMError("timeout"), 500])

    script = generate_script(CFG, "reviews", "long")

    assert word_count(script) == 600
    assert requests[2].count("Topic: reviews") == 1     # retry text does not pile up
    assert "an attempt failed (timeout)" in capsys.readouterr().out


def test_every_attempt_failing_raises(monkeypatch):
    _fake_llm(monkeypatch, [LLMError("down")] * 3)

    with pytest.raises(script_module.ScriptError):
        generate_script(CFG, "reviews", "long")


def test_short_format_is_not_length_checked(monkeypatch):
    requests = _fake_llm(monkeypatch, [40])

    assert word_count(generate_script(CFG, "reviews", "short")) == 40
    assert len(requests) == 1


def test_rvc_off_by_default_and_part_of_voice_id_when_on(monkeypatch, tmp_path):
    calls = {}
    fake = types.SimpleNamespace(
        synthesize=lambda text, out, **kw: calls.update(kw) or "styletts2",
        AllEnginesFailedError=RuntimeError)
    monkeypatch.setitem(sys.modules, "voiceclone", fake)
    monkeypatch.setattr(voice, "clone_available", lambda: True)
    monkeypatch.setattr(voice, "_pinned_clone_engine", None)
    off = replace(CFG, voice={**CFG.voice, "provider": "clone", "clone_rvc": "off"})
    on = replace(CFG, voice={**CFG.voice, "provider": "clone", "clone_rvc": "auto"})

    voice._clone(off, "hi", tmp_path / "v.wav")

    assert calls["rvc"] == "off"
    assert CFG.voice["clone_rvc"] == "off"
    assert "rvc" not in voice.voice_id(off) and voice.voice_id(on).endswith(":rvc-auto")
