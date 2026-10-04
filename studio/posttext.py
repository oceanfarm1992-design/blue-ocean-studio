"""Titles, descriptions and hashtags for posts, shared by Buffer and YouTube publishing."""
from __future__ import annotations

import re
from pathlib import Path

from studio.config import Config
from studio.metadata import build_chapters
from studio.shorts import scene_durations

YOUTUBE_TITLE_MAX = 100
YOUTUBE_TEXT_MAX = 4900
SOCIAL_TEXT_MAX = 2900  # LinkedIn's limit is 3000; Facebook and Instagram allow more


def hashtags(tags: list[str], limit: int) -> str:
    cleaned = []
    for tag in tags:
        word = re.sub(r"[^A-Za-z0-9]", "", tag.title())
        if word and word.lower() not in {c.lower() for c in cleaned}:
            cleaned.append(word)
    return " ".join(f"#{word}" for word in cleaned[:limit])


def youtube_title(script: dict) -> str:
    title = script["title"]
    if script["format"] == "short" and "#shorts" not in title.lower():
        title = f"{title} #Shorts"
    return title[:YOUTUBE_TITLE_MAX].rstrip()


def youtube_text(script: dict, cta: str, chapters: list[str]) -> str:
    parts = [script["description"], cta]
    if chapters:
        parts.append("Chapters:\n" + "\n".join(chapters))
    return "\n\n".join(p for p in parts if p)[:YOUTUBE_TEXT_MAX]


def social_text(script: dict, cta: str, tags_line: str) -> str:
    first_paragraph = script["description"].split("\n")[0]
    parts = [script["title"], first_paragraph, cta, tags_line]
    return "\n\n".join(p for p in parts if p)[:SOCIAL_TEXT_MAX]


def build_texts(cfg: Config, project_dir: Path, script: dict) -> dict[str, str]:
    settings = cfg.publish or {}
    is_long = script["format"] == "long"
    cta = cfg.channel["cta"] if is_long else cfg.channel["follow_line"]
    chapters = []
    if is_long:
        durations = scene_durations(project_dir, script)
        chapters = build_chapters([(s["section"], d) for s, d in zip(script["scenes"], durations)])
    tags_line = hashtags(script["tags"], int(settings.get("max_hashtags", 5)))
    return {"youtube": youtube_text(script, cta, chapters),
            "social": social_text(script, cta, tags_line)}
