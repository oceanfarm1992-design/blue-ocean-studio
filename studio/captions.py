"""Word timings and burned-in caption (ASS subtitle) generation."""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_GAP_SECONDS = 0.35   # a pause longer than this starts a new caption
TAIL_SECONDS = 0.3       # how long the last caption stays after the last word
MAX_LINGER_SECONDS = 0.6 # a caption never stays this long past its last word
LOOKAHEAD_TOKENS = 3     # how far ahead to search when matching spoken words to script text


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class CaptionStyle:
    size: int
    margin_v: int
    max_words: int
    outline: int
    uppercase: bool


LONG_STYLE = CaptionStyle(size=58, margin_v=70, max_words=6, outline=3, uppercase=False)
SHORT_STYLE = CaptionStyle(size=96, margin_v=620, max_words=3, outline=6, uppercase=True)


def estimate_word_timings(text: str, duration: float) -> tuple[Word, ...]:
    """Spreads words over the duration in proportion to their length.

    Used when the voice provider gives no word timestamps.
    """
    tokens = text.split()
    if not tokens or duration <= 0:
        return ()
    weights = [len(token) + 1 for token in tokens]
    total = sum(weights)
    words = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * weight / total
        words.append(Word(token, round(cursor, 3), round(cursor + span, 3)))
        cursor += span
    return tuple(words)


def _normalize(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", token.lower())


def restore_punctuation(words: tuple[Word, ...], text: str) -> tuple[Word, ...]:
    """Replaces bare spoken words with the script's spelling and punctuation."""
    tokens = text.split()
    restored = []
    next_token = 0
    for word in words:
        target = _normalize(word.text)
        match = None
        for k in range(next_token, min(next_token + LOOKAHEAD_TOKENS, len(tokens))):
            if _normalize(tokens[k]) == target:
                match = k
                break
        if match is None:
            restored.append(word)
            continue
        restored.append(Word(tokens[match], word.start, word.end))
        next_token = match + 1
    return tuple(restored)


def chunk_words(words: tuple[Word, ...], max_words: int,
                max_gap: float = MAX_GAP_SECONDS) -> list[tuple[Word, ...]]:
    """Groups words into caption lines, breaking on size, pauses and punctuation."""
    chunks: list[tuple[Word, ...]] = []
    current: list[Word] = []
    for word in words:
        if current and (len(current) >= max_words or word.start - current[-1].end > max_gap):
            chunks.append(tuple(current))
            current = []
        current.append(word)
        if re.search(r"[.!?,;:]$", word.text):
            chunks.append(tuple(current))
            current = []
    if current:
        chunks.append(tuple(current))
    return chunks


def format_ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, rem = divmod(centis, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def escape_ass_text(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _ass_header(width: int, height: int, font: str, style: CaptionStyle) -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{style.size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{style.outline},1,2,60,60,{style.margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def to_ass(words: tuple[Word, ...], width: int, height: int, font: str,
           style: CaptionStyle) -> str:
    chunks = chunk_words(words, style.max_words)
    events = []
    for i, chunk in enumerate(chunks):
        start = chunk[0].start
        if i + 1 < len(chunks):
            end = min(chunks[i + 1][0].start, chunk[-1].end + MAX_LINGER_SECONDS)
        else:
            end = chunk[-1].end + TAIL_SECONDS
        text = " ".join(word.text for word in chunk)
        if style.uppercase:
            text = text.upper()
        events.append(
            f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(max(end, start + 0.1))},"
            f"Default,,0,0,0,,{escape_ass_text(text)}"
        )
    return _ass_header(width, height, font, style) + "\n".join(events) + "\n"
