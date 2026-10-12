from dataclasses import replace

from studio import voice
from studio.config import load_config

CFG = load_config()


def _with(provider: str):
    return replace(CFG, voice={**CFG.voice, "provider": provider})


def test_clone_falls_back_when_token_missing(monkeypatch):
    monkeypatch.delenv("VOICE_REPO_PAT", raising=False)
    cfg = _with("clone")

    assert voice.effective_provider(cfg) == cfg.voice["fallback_provider"]
    assert voice.audio_filename(cfg) == "voice.mp3"
    assert voice.voice_id(cfg).startswith("edge:")


def test_clone_used_when_package_and_token_present(monkeypatch):
    monkeypatch.setenv("VOICE_REPO_PAT", "token")
    monkeypatch.setattr(voice, "clone_available", lambda: True)
    cfg = _with("clone")

    assert voice.effective_provider(cfg) == "clone"
    assert voice.audio_filename(cfg) == "voice.wav"
    assert voice.voice_id(cfg) == (f"clone:{cfg.voice['clone_repo']}/{cfg.voice['clone_file']}"
                                   f":presentation:{voice.AUDIO_VERSION}")


def test_other_providers_unchanged():
    assert voice.effective_provider(_with("edge")) == "edge"
    assert voice.voice_id(_with("openai")).startswith("openai:")


def test_clone_style_changes_voice_id_and_is_passed_to_engine(monkeypatch, tmp_path):
    import sys
    import types

    calls = {}

    def fake_synthesize(text, out, **kwargs):
        calls.update(kwargs)
        return "styletts2"

    fake = types.SimpleNamespace(synthesize=fake_synthesize, AllEnginesFailedError=RuntimeError)
    monkeypatch.setitem(sys.modules, "voiceclone", fake)
    monkeypatch.setattr(voice, "clone_available", lambda: True)
    monkeypatch.setattr(voice, "_pinned_clone_engine", None)
    talking = replace(CFG, voice={**CFG.voice, "provider": "clone", "clone_style": "talking"})
    plain = replace(CFG, voice={**CFG.voice, "provider": "clone", "clone_style": "default"})

    voice._clone(talking, "hello", tmp_path / "v.wav")

    assert calls["style"] == "talking"
    assert voice.voice_id(talking) != voice.voice_id(plain)


def _tone_with_silence(path, lead: float, tone: float, trail: float) -> None:
    import subprocess

    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={lead}",
                    "-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=24000:duration={tone}",
                    "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={trail}",
                    "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(path)], check=True)


def test_silent_edges_are_trimmed_and_word_times_shift(tmp_path):
    import shutil

    import pytest

    if shutil.which("ffmpeg") is None:
        pytest.skip("needs FFmpeg")
    from studio.captions import Word

    audio = tmp_path / "voice.mp3"
    _tone_with_silence(audio, lead=0.7, tone=1.0, trail=1.6)
    words = [Word("hi", 0.72, 1.2), Word("there", 1.2, 1.7)]

    duration, shifted = voice._trim_edges(audio, words)

    keep = 2 * voice.EDGE_KEEP_SECONDS
    assert duration == pytest.approx(1.0 + keep, abs=0.12)     # was 3.3 s
    assert shifted[0].start == pytest.approx(0.72 - (0.7 - voice.EDGE_KEEP_SECONDS), abs=0.05)


def test_audio_without_silent_edges_is_left_alone(tmp_path):
    import shutil

    import pytest

    if shutil.which("ffmpeg") is None:
        pytest.skip("needs FFmpeg")
    audio = tmp_path / "voice.mp3"
    _tone_with_silence(audio, lead=0.01, tone=1.0, trail=0.01)
    before = audio.read_bytes()

    voice._trim_edges(audio, [])

    assert audio.read_bytes() == before
