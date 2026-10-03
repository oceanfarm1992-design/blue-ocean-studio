"""Upload metadata: description with chapters, tags and footage credits."""
from __future__ import annotations

MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10.0


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def build_chapters(sections: list[tuple[str, float]]) -> list[str]:
    """Turns (section, duration) per scene into YouTube chapter lines.

    Consecutive scenes with the same section merge into one chapter. Returns an
    empty list when YouTube would reject the chapters (fewer than 3, or any under 10 s).
    """
    merged: list[tuple[str, float, float]] = []  # (name, start, length)
    cursor = 0.0
    for name, duration in sections:
        if merged and merged[-1][0] == name:
            prev_name, start, length = merged[-1]
            merged[-1] = (prev_name, start, length + duration)
        else:
            merged.append((name, cursor, duration))
        cursor += duration
    if len(merged) < MIN_CHAPTERS or any(length < MIN_CHAPTER_SECONDS for _, _, length in merged):
        return []
    return [f"{format_timestamp(start)} {name.title()}" for name, start, _ in merged]


def build_metadata(script: dict, cta: str, chapters: list[str], credits: list[str]) -> str:
    parts = [
        "TITLE",
        script["title"],
        "",
        "OTHER TITLE OPTIONS",
        *script["title_options"],
        "",
        "DESCRIPTION",
        script["description"],
        "",
        cta,
    ]
    if chapters:
        parts += ["", "Chapters:", *chapters]
    if credits:
        parts += ["", "Stock footage:", *sorted(set(credits))]
    parts += ["", "TAGS", ", ".join(script["tags"])]
    return "\n".join(parts) + "\n"
