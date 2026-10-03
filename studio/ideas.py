"""Generates a backlog of video ideas."""
from __future__ import annotations

from studio.config import Config
from studio.llm import complete_json


def build_ideas_prompt(cfg: Config, count: int, focus: str) -> tuple[str, str]:
    ch = cfg.channel
    system = (
        f"You plan content for \"{ch['name']}\", a faceless YouTube channel.\n"
        f"Niche: {ch['niche']}\nAudience: {ch['audience']}\n"
        "Pick topics that small business owners actually search for: specific problems, "
        "not vague inspiration. Spread ideas across many business types. Return only JSON."
    )
    focus_line = f"Focus on: {focus}\n" if focus else ""
    user = (
        f"Give {count} video ideas. About 1 in 3 should be short-form.\n{focus_line}"
        'Return JSON: {"ideas": [{"title": "...", "business_type": "...", '
        '"format": "long or short", "hook": "first sentence of the video", '
        '"why": "the search demand or pain behind it"}]}'
    )
    return system, user


def clean_ideas(data: object) -> list[dict]:
    raw = data.get("ideas", []) if isinstance(data, dict) else []
    ideas = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("title", "")).strip():
            continue
        fmt = str(item.get("format", "long")).strip().lower()
        ideas.append({
            "title": str(item["title"]).strip(),
            "business_type": str(item.get("business_type", "")).strip(),
            "format": fmt if fmt in ("long", "short") else "long",
            "hook": str(item.get("hook", "")).strip(),
            "why": str(item.get("why", "")).strip(),
        })
    return ideas


def generate_ideas(cfg: Config, count: int = 20, focus: str = "") -> list[dict]:
    system, user = build_ideas_prompt(cfg, count, focus)
    return clean_ideas(complete_json(cfg, system, user))


def ideas_to_markdown(ideas: list[dict]) -> str:
    lines = ["# Video ideas", "", "| # | Format | Business | Title | Hook |", "|---|---|---|---|---|"]
    for i, idea in enumerate(ideas, start=1):
        cells = [str(i), idea["format"], idea["business_type"], idea["title"], idea["hook"]]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return "\n".join(lines) + "\n"
