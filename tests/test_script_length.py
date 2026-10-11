"""Long scripts must be long enough for an 8-minute video; the cloned voice skips RVC."""
import sys
import types
from dataclasses import replace

from studio import script as script_module
from studio import voice
from studio.config import load_config
from studio.script import generate_script, min_long_words, word_count

CFG = load_config()


def _draft(words: int) -> dict:
    return {"title": "T", "scenes": [{"narration": " ".join(["word"] * words)}]}


def _fake_llm(monkeypatch, lengths: list[int]) -> list[str]:
    requests = []

    def fake(cfg, system, user):
        requests.append(user)
        return _draft(lengths[len(requests) - 1])

    monkeypatch.setattr(script_module, "complete_json", fake)
    return requests


def test_prompt_asks_for_more_than_the_minimum():
    _, user = script_module.build_prompt(CFG, "reviews", "long")
    asked = int(min_long_words(CFG) * script_module.LENGTH_MARGIN)

    assert f"at least {asked} spoken words" in user
    assert asked > min_long_words(CFG)


def test_short_long_script_is_sent_back_with_its_word_count(monkeypatch):
    enough = min_long_words(CFG)
    requests = _fake_llm(monkeypatch, [600, enough])

    script = generate_script(CFG, "reviews", "long")

    assert word_count(script) == enough
    assert len(requests) == 2 and "only 600 spoken words" in requests[1]


def test_longest_draft_used_when_every_attempt_is_short(monkeypatch, capsys):
    requests = _fake_llm(monkeypatch, [500, 900, 700])

    script = generate_script(CFG, "reviews", "long")

    assert word_count(script) == 900 and len(requests) == script_module.LONG_ATTEMPTS
    assert "WARNING" in capsys.readouterr().out


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
