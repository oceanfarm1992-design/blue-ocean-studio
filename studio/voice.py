"""Text-to-speech.

Providers:
- "clone": the channel owner's own voice, cloned with StyleTTS2 from a private reference
  sample (package styletts2-voice-clone, installed in the cloud from requirements-cloud.txt).
  Needs VOICE_REPO_PAT. Where the package or token is missing, e.g. on the PC, it falls
  back to `fallback_provider`.
- "edge": free Microsoft neural voices, with exact word timings.
- "openai": OpenAI TTS, about $0.015 per minute.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from studio.captions import Word, estimate_word_timings, restore_punctuation
from studio.config import Config
from studio.media import probe_duration

TICKS_PER_SECOND = 10_000_000  # edge-tts reports offsets in 100 ns units
CLONE_ENGINE = "styletts2"      # the engine that actually uses the owner's voice

# The clone package falls back through other engines if StyleTTS2 fails. Whichever engine
# voices the first scene is pinned for the rest of the run so a video never mixes voices.
_pinned_clone_engine: str | None = None
_warned_fallback = False


class VoiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceResult:
    audio_path: Path
    duration: float
    words: tuple[Word, ...]


def clone_available() -> bool:
    return (importlib.util.find_spec("voiceclone") is not None
            and bool(os.environ.get("VOICE_REPO_PAT", "").strip()))


def effective_provider(cfg: Config) -> str:
    """The provider that will really be used on this machine."""
    provider = cfg.voice["provider"]
    if provider == "clone" and not clone_available():
        return cfg.voice.get("fallback_provider", "edge")
    return provider


def audio_filename(cfg: Config) -> str:
    return "voice.wav" if effective_provider(cfg) == "clone" else "voice.mp3"


def voice_id(cfg: Config) -> str:
    """Identifies the voice settings so cached audio is redone when they change."""
    v = cfg.voice
    provider = effective_provider(cfg)
    if provider == "clone":
        return f"clone:{v['clone_repo']}/{v['clone_file']}"
    if provider == "openai":
        return f"openai:{v['openai_model']}:{v['openai_voice']}"
    return f"edge:{v['edge_voice']}:{v['edge_rate']}"


async def _edge_stream(text: str, voice: str, rate: str, out_path: Path) -> list[Word]:
    import edge_tts

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    except TypeError:  # edge-tts < 7 has no boundary argument and sends words by default
        communicate = edge_tts.Communicate(text, voice, rate=rate)

    words: list[Word] = []
    with out_path.open("wb") as audio:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / TICKS_PER_SECOND
                end = start + chunk["duration"] / TICKS_PER_SECOND
                words.append(Word(chunk["text"], round(start, 3), round(end, 3)))
    return words


def _edge(cfg: Config, text: str, out_path: Path) -> list[Word]:
    try:
        return asyncio.run(
            _edge_stream(text, cfg.voice["edge_voice"], cfg.voice["edge_rate"], out_path)
        )
    except Exception as exc:  # edge-tts raises several unrelated network error types
        raise VoiceError(f"Edge voice failed: {exc}") from exc


def _openai(cfg: Config, text: str, out_path: Path) -> list[Word]:
    if not cfg.openai_key:
        raise VoiceError("OpenAI voice selected but no OpenAI key found.")
    from openai import OpenAI, OpenAIError

    client = OpenAI(api_key=cfg.openai_key)
    try:
        with client.audio.speech.with_streaming_response.create(
            model=cfg.voice["openai_model"],
            voice=cfg.voice["openai_voice"],
            input=text,
            response_format="mp3",
        ) as response:
            response.stream_to_file(out_path)
    except OpenAIError as exc:
        raise VoiceError(f"OpenAI voice failed: {exc}") from exc
    return []  # no timestamps; estimated afterwards


def _clone(cfg: Config, text: str, out_path: Path) -> list[Word]:
    global _pinned_clone_engine
    from voiceclone import AllEnginesFailedError
    from voiceclone import synthesize as clone_synthesize

    v = cfg.voice
    try:
        used = clone_synthesize(
            text, str(out_path), engine=_pinned_clone_engine or "auto",
            voice_ref_repo=v["clone_repo"], voice_ref_file=v["clone_file"],
            voice_ref_cache=str(cfg.cache_dir / "voice_reference.mp3"))
    except AllEnginesFailedError as exc:
        raise VoiceError(f"Cloned voice failed: {exc}") from exc
    if _pinned_clone_engine is None:
        _pinned_clone_engine = used
        if used != CLONE_ENGINE:
            print(f"  WARNING: your cloned voice failed to load; this video uses '{used}'.")
    return []  # no timestamps; estimated afterwards


def synthesize(cfg: Config, text: str, out_path: Path) -> VoiceResult:
    global _warned_fallback
    out_path.parent.mkdir(parents=True, exist_ok=True)
    provider = effective_provider(cfg)
    if provider != cfg.voice["provider"] and not _warned_fallback:
        print(f"  note: cloned voice not available here, using '{provider}' instead")
        _warned_fallback = True
    if provider == "clone":
        words = _clone(cfg, text, out_path)
    elif provider == "edge":
        words = _edge(cfg, text, out_path)
    elif provider == "openai":
        words = _openai(cfg, text, out_path)
    else:
        raise VoiceError(f"Unknown voice provider '{provider}'. Use clone, edge or openai.")

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise VoiceError("Voice provider returned no audio.")
    duration = probe_duration(out_path)
    timed = restore_punctuation(tuple(words), text) if words else estimate_word_timings(text, duration)
    return VoiceResult(out_path, duration, timed)
