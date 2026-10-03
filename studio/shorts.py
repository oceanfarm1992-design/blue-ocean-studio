"""Turns the step sections of a long video into vertical Shorts, with no extra API cost."""
from __future__ import annotations

import json
from pathlib import Path

from studio.script import WORDS_PER_MINUTE, validate_script

SKIP_SECTIONS = {"HOOK", "STAKES", "RECAP", "CTA", "INTRO", "OUTRO"}
MIN_SHORT_SECONDS = 15.0
MAX_SHORT_SECONDS = 75.0  # leaves room for the closing line within ~90 s
DEFAULT_MAX_SHORTS = 5
MAX_TITLE_LENGTH = 90


def estimate_duration(narration: str) -> float:
    return len(narration.split()) / WORDS_PER_MINUTE * 60


def scene_durations(project_dir: Path, script: dict) -> list[float]:
    """Real durations from a previous render where available, else word-count estimates."""
    durations = []
    for i, scene in enumerate(script["scenes"], start=1):
        state_path = project_dir / "build" / f"scene_{i:02d}" / "state.json"
        duration = None
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("narration") == scene["narration"]:
                    duration = float(state["duration"])
            except (json.JSONDecodeError, KeyError, ValueError):
                duration = None
        durations.append(duration if duration is not None else estimate_duration(scene["narration"]))
    return durations


def group_sections(scenes: list[dict], durations: list[float]) -> list[tuple[str, list[int], float]]:
    """Consecutive scenes sharing a section: (section, scene indexes, total seconds)."""
    groups: list[tuple[str, list[int], float]] = []
    for i, (scene, duration) in enumerate(zip(scenes, durations)):
        if groups and groups[-1][0] == scene["section"]:
            name, indexes, total = groups[-1]
            groups[-1] = (name, [*indexes, i], total + duration)
        else:
            groups.append((scene["section"], [i], duration))
    return groups


def _short_title(long_title: str, label: str) -> str:
    title = f"{label} | {long_title}" if label else long_title
    return title[:MAX_TITLE_LENGTH].rstrip()


def plan_shorts(script: dict, durations: list[float], follow_line: str,
                max_shorts: int = DEFAULT_MAX_SHORTS) -> list[dict]:
    """Builds short-format scripts from the usable sections of a long script."""
    shorts = []
    for section, indexes, total in group_sections(script["scenes"], durations):
        if len(shorts) >= max_shorts:
            break
        if section in SKIP_SECTIONS or not MIN_SHORT_SECONDS <= total <= MAX_SHORT_SECONDS:
            continue
        scenes = [dict(script["scenes"][i]) for i in indexes]
        label = scenes[0]["on_screen_text"]
        scenes.append({
            "section": "FOLLOW",
            "narration": follow_line,
            "visual_query": scenes[-1]["visual_query"],
            "on_screen_text": "Follow for daily fixes",
        })
        shorts.append(validate_script({
            "topic": f"{script['topic']} ({section.title()})",
            "title": _short_title(script["title"], label),
            "title_options": [],
            "thumbnail_text": label or script["thumbnail_text"],
            "description": f"{label}. From our full video: {script['title']}.",
            "tags": [*script["tags"], "shorts"],
            "scenes": scenes,
        }, "short"))
    return shorts
