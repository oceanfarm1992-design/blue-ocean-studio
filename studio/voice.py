"""Text-to-speech. Edge voices are free; OpenAI TTS is a paid fallback."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from studio.captions import Word, estimate_word_timings, restore_punctuation
from studio.config import Config
from studio.media import probe_duration

TICKS_PER_SECOND = 10_000_000  # edge-tts reports offsets in 100 ns units


class VoiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceResult:
    audio_path: Path
    duration: float
    words: tuple[Word, ...]


def voice_id(cfg: Config) -> str:
    """Identifies the voice settings so cached audio is redone when they change."""
    v = cfg.voice
    if v["provider"] == "openai":
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


def synthesize(cfg: Config, text: str, out_path: Path) -> VoiceResult:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    provider = cfg.voice["provider"]
    if provider == "edge":
        words = _edge(cfg, text, out_path)
    elif provider == "openai":
        words = _openai(cfg, text, out_path)
    else:
        raise VoiceError(f"Unknown voice provider '{provider}'. Use 'edge' or 'openai'.")

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise VoiceError("Voice provider returned no audio.")
    duration = probe_duration(out_path)
    timed = restore_punctuation(tuple(words), text) if words else estimate_word_timings(text, duration)
    return VoiceResult(out_path, duration, timed)
