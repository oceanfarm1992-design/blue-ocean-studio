"""Writes, validates and formats video scripts."""
from __future__ import annotations

from studio.config import Config
from studio.doodle import settings as doodle_settings
from studio.llm import LLMError, complete_json

FORMATS = ("long", "short")
VISUALS = ("stock", "doodle")
CHECK_MARKER = "[CHECK"
WORDS_PER_MINUTE = 150
LENGTH_MARGIN = 1.1  # ask for 10% more than the minimum: models tend to write short
LONG_ATTEMPTS = 3


class ScriptError(ValueError):
    pass


def _format_rules(cfg: Config, fmt: str) -> str:
    if fmt == "long":
        words = int(min_long_words(cfg) * LENGTH_MARGIN)
        return (
            f"FORMAT: long-form YouTube video, at least {words} spoken words in total "
            f"(this is required: videos under {cfg.video['long_minutes']} minutes lose mid-roll ads).\n"
            "- 25 to 40 scenes, each 25 to 55 words of narration.\n"
            "- Structure: HOOK (1-2 scenes, under 20 seconds, open with a concrete problem or "
            "surprising number), STAKES, 3 to 6 numbered STEP sections with a real example each, "
            "a MISTAKE section, RECAP, CTA.\n"
            f"- The final scene must end with this call to action: \"{cfg.channel['cta']}\""
        )
    seconds = cfg.video["short_seconds"]
    words = int(seconds / 60 * WORDS_PER_MINUTE)
    return (
        f"FORMAT: vertical short (YouTube Shorts, TikTok, Reels), about {seconds} seconds, "
        f"about {words} spoken words in total.\n"
        "- 4 to 7 scenes, each 10 to 25 words.\n"
        "- Scene 1 is the hook: under 10 words, a bold claim or a question.\n"
        "- One single tip, explained with one concrete example.\n"
        f"- The final scene ends with: \"{cfg.channel['follow_line']}\""
    )


def _visual_rules(cfg: Config) -> tuple[str, str]:
    """Extra scene keys and rules when doodle scenes are mixed with stock footage."""
    if doodle_settings(cfg)["mode"] != "mix":
        return "", ""
    keys = ', "visual": "stock or doodle", "doodle_prompt": "what to draw"'
    rules = (
        "\n\nvisual rules: each scene is either \"stock\" (real stock footage) or \"doodle\" "
        "(a whiteboard drawing sketched on screen). Use doodle for the hook, ideas, offers, "
        "prices, numbers, steps, before/after and anything a camera cannot film well. Use "
        "stock for real people doing real things (a stylist blow-drying hair, a baker at the "
        "counter). Make roughly half the scenes doodles and avoid more than two of the same "
        "kind in a row.\n"
        "doodle_prompt rules (required for doodle scenes, empty for stock): one simple picture "
        "a whiteboard artist could sketch in 10 seconds, 1 to 3 people or objects with a clear "
        "action, e.g. \"stylist next to an empty salon chair, wall clock\" or \"phone sending "
        "messages to nearby houses, map pin\". Never ask for words, numbers, prices or logos "
        "in the picture: those go in on_screen_text."
    )
    return keys, rules


def build_prompt(cfg: Config, topic: str, fmt: str) -> tuple[str, str]:
    ch = cfg.channel
    visual_keys, visual_rules = _visual_rules(cfg)
    system = (
        f"You are the head writer for \"{ch['name']}\", a faceless YouTube channel.\n"
        f"Niche: {ch['niche']}\nAudience: {ch['audience']}\n\n"
        "Write scripts that are original, specific and genuinely useful:\n"
        "- Plain spoken English, short sentences, second person (\"you\"). No markdown, "
        "no emojis, no stage directions inside narration.\n"
        "- Give concrete examples for real types of small business (a bakery, a plumber, "
        "a nail salon), with real steps, costs and wording people can copy.\n"
        "- Prefer free or cheap tactics. Never promise guaranteed results.\n"
        "- Never invent statistics or studies. If a number is not common knowledge, wrap that "
        "sentence like [CHECK: sentence] so a human verifies it before recording.\n"
        "- Do not name real local businesses or people.\n"
        "Return only JSON."
    )
    user = (
        f"Topic: {topic}\n\n{_format_rules(cfg, fmt)}\n\n"
        "Return JSON with exactly these keys:\n"
        "{\n"
        '  "title": "best YouTube title, under 70 characters",\n'
        '  "title_options": ["3 alternative titles"],\n'
        '  "thumbnail_text": "2 to 5 punchy words for the thumbnail",\n'
        '  "description": "2 short paragraphs for the video description, no links",\n'
        '  "tags": ["8 to 15 search tags"],\n'
        '  "scenes": [\n'
        '    {"section": "HOOK", "narration": "spoken words", '
        '"visual_query": "stock footage search", "on_screen_text": "key point, max 6 words"'
        f'{visual_keys}}}\n'
        "  ]\n"
        "}\n\n"
        "visual_query rules: 2 to 5 words describing a scene a camera could film: who, doing "
        "what, where. Tie it to the business type in the topic, e.g. \"plumber answering phone\", "
        "\"baker handing customer box\", \"woman reading phone reviews\". Never use abstract "
        "concepts (strategy, call to action, SEO, routing, growth), screens of code, or brand "
        "names. Vary the scenes so consecutive ones look different."
        f"{visual_rules}"
    )
    return system, user


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _validate_scene(index: int, scene: object, fallback_query: str) -> dict:
    if not isinstance(scene, dict):
        raise ScriptError(f"Scene {index} is not an object.")
    narration = str(scene.get("narration", "")).strip()
    if not narration:
        raise ScriptError(f"Scene {index} has no narration.")
    visual = str(scene.get("visual", "")).strip().lower()
    return {
        "section": str(scene.get("section", "")).strip().upper() or "MAIN",
        "narration": narration,
        "visual_query": str(scene.get("visual_query", "")).strip() or fallback_query,
        "on_screen_text": str(scene.get("on_screen_text", "")).strip(),
        "visual": visual if visual in VISUALS else "stock",
        "doodle_prompt": str(scene.get("doodle_prompt", "")).strip(),
    }


def validate_script(data: object, fmt: str, topic: str = "") -> dict:
    """Returns a cleaned copy of the script or raises ScriptError."""
    if fmt not in FORMATS:
        raise ScriptError(f"Unknown format '{fmt}'. Use one of: {', '.join(FORMATS)}.")
    if not isinstance(data, dict):
        raise ScriptError("Script must be a JSON object.")
    title = str(data.get("title", "")).strip()
    if not title:
        raise ScriptError("Script has no title.")
    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ScriptError("Script has no scenes.")

    fallback_query = "small business owner"
    scenes = [_validate_scene(i + 1, s, fallback_query) for i, s in enumerate(raw_scenes)]
    return {
        "format": fmt,
        "topic": str(data.get("topic", topic)).strip() or title,
        "title": title,
        "title_options": _string_list(data.get("title_options")),
        "thumbnail_text": str(data.get("thumbnail_text", "")).strip() or title,
        "description": str(data.get("description", "")).strip(),
        "tags": _string_list(data.get("tags")),
        "scenes": scenes,
    }


def unchecked_scenes(script: dict) -> list[int]:
    """Scene numbers that still contain a [CHECK: ...] marker."""
    return [i + 1 for i, s in enumerate(script["scenes"]) if CHECK_MARKER in s["narration"]]


def min_long_words(cfg: Config) -> int:
    """Spoken words a long video needs to reach long_minutes (mid-roll ads need 8+)."""
    return int(cfg.video["long_minutes"] * WORDS_PER_MINUTE)


def generate_script(cfg: Config, topic: str, fmt: str, attempts: int = 2) -> dict:
    """A long script that comes back too short is sent back with its real word count;
    after the last attempt the longest draft is used, with a warning."""
    system, user = build_prompt(cfg, topic, fmt)
    attempts = max(attempts, LONG_ATTEMPTS) if fmt == "long" else attempts
    last_error: Exception | None = None
    longest: dict | None = None
    request = user
    for _ in range(attempts):
        try:
            script = validate_script(complete_json(cfg, system, request), fmt, topic)
        except (ScriptError, LLMError) as exc:
            last_error = exc
            print(f"  retrying: {exc}")
            continue
        if fmt != "long" or word_count(script) >= min_long_words(cfg):
            return script
        if longest is None or word_count(script) > word_count(longest):
            longest = script
        needed = int(min_long_words(cfg) * LENGTH_MARGIN)
        print(f"  script too short ({word_count(script)} words), asking for {needed}")
        request = (f"{user}\n\nYour previous draft had only {word_count(script)} spoken words. "
                   f"Write at least {needed}: add scenes and concrete examples, do not pad.")
    if longest is not None:
        print(f"  WARNING: long script has {word_count(longest)} words, under "
              f"{min_long_words(cfg)}; the video may be shorter than "
              f"{cfg.video['long_minutes']} minutes")
        return longest
    raise ScriptError(f"Could not get a valid script: {last_error}")


def word_count(script: dict) -> int:
    return sum(len(s["narration"].split()) for s in script["scenes"])


def _visual_line(scene: dict) -> str:
    if scene.get("visual") == "doodle":
        shown = f"*Doodle:* {scene.get('doodle_prompt') or scene['visual_query']}"
    else:
        shown = f"*Footage:* {scene['visual_query']}"
    return f"{shown} · *On screen:* {scene['on_screen_text']}"


def to_markdown(script: dict) -> str:
    minutes = word_count(script) / WORDS_PER_MINUTE
    lines = [
        f"# {script['title']}",
        "",
        f"Format: {script['format']} · {word_count(script)} words · about {minutes:.1f} min",
        "",
        "## Title options",
        *[f"- {t}" for t in script["title_options"]],
        "",
        f"Thumbnail text: **{script['thumbnail_text']}**",
        "",
        "## Script",
    ]
    for i, scene in enumerate(script["scenes"], start=1):
        lines += [
            "",
            f"### {i:02d} · {scene['section']}",
            scene["narration"],
            _visual_line(scene),
        ]
    lines += ["", "## Description", script["description"], "", "## Tags", ", ".join(script["tags"])]
    return "\n".join(lines) + "\n"
