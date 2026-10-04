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
    assert voice.voice_id(cfg) == f"clone:{cfg.voice['clone_repo']}/{cfg.voice['clone_file']}"


def test_other_providers_unchanged():
    assert voice.effective_provider(_with("edge")) == "edge"
    assert voice.voice_id(_with("openai")).startswith("openai:")
