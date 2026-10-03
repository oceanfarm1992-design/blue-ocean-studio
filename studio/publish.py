"""Sends a rendered project to Buffer: hosts the video, then creates one post per channel."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from studio.buffer import BufferClient
from studio.config import Config
from studio.hosting import (
    HostingError,
    fit_under_limit,
    parse_cloudinary_url,
    public_id_from_url,
    upload_video,
)
from studio.metadata import build_chapters
from studio.project import load_script
from studio.script import unchecked_scenes
from studio.shorts import scene_durations

YOUTUBE_TITLE_MAX = 100
YOUTUBE_TEXT_MAX = 4900
SOCIAL_TEXT_MAX = 2900  # LinkedIn's limit is 3000; Facebook allows far more
SUPPORTED_SERVICES = ("youtube", "facebook", "linkedin", "instagram", "tiktok")


class PublishError(RuntimeError):
    pass


def parse_when(when: str, tz_name: str | None = None) -> tuple[str, str | None]:
    """'queue', 'now' or a date/time -> (Buffer share mode, ISO UTC due date or None).

    A time without an offset is read in tz_name (the channel's time zone), or in this
    computer's time zone when none is set.
    """
    if when == "queue":
        return "addToQueue", None
    if when == "now":
        return "shareNow", None
    try:
        moment = datetime.fromisoformat(when)
    except ValueError as exc:
        raise PublishError(f"Can't read the time '{when}'. Use e.g. 2026-10-06T15:00") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(tz_name)) if tz_name else moment.astimezone()
    utc = moment.astimezone(timezone.utc)
    return "customScheduled", utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


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


def _service_metadata(service: str, script: dict, settings: dict) -> dict | None:
    is_short = script["format"] == "short"
    disclose = bool(settings.get("disclose_ai", False))
    if service == "youtube":
        return {"youtube": {
            "title": youtube_title(script),
            "categoryId": str(settings.get("youtube_category", "27")),
            "privacy": settings.get("youtube_privacy", "public"),
            "madeForKids": False,
            "notifySubscribers": True,
            "isAiGenerated": disclose,
        }}
    if service == "facebook":
        return {"facebook": {"type": "reel" if is_short else "post"}}
    if service == "instagram":
        return {"instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": disclose}}
    if service == "tiktok":
        return {"tiktok": {"isAiGenerated": disclose}}
    if service == "linkedin":
        return None
    raise PublishError(f"Posting to '{service}' is not supported yet.")


def build_post_input(service: str, channel_id: str, script: dict, texts: dict[str, str],
                     video_url: str, mode: str, due_at: str | None, draft: bool,
                     settings: dict) -> dict:
    post = {
        "channelId": channel_id,
        "text": texts["youtube"] if service == "youtube" else texts["social"],
        "schedulingType": "automatic",
        "mode": mode,
        "needsApproval": False,
        "saveToDraft": draft,
        "aiAssisted": True,
        "assets": [{"video": {"url": video_url}}],
    }
    if due_at:
        post["dueAt"] = due_at
    metadata = _service_metadata(service, script, settings)
    if metadata:
        post["metadata"] = metadata
    return post


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


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {"posts": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _host_video(cfg: Config, project_dir: Path, state: dict) -> dict:
    """Returns state with a hosted video URL, uploading only when there isn't a live one."""
    if state.get("video_url") and not state.get("hosting_deleted"):
        return state
    creds = parse_cloudinary_url(cfg.cloudinary_url)
    if creds is None:
        raise HostingError("No valid CLOUDINARY_URL found. Add it to .env.txt "
                           "(format: cloudinary://API_KEY:API_SECRET@CLOUD_NAME).")
    upload_file = fit_under_limit(project_dir / "video.mp4", project_dir / "build")
    print("  uploading video to Cloudinary...")
    url = upload_video(creds, upload_file, project_dir.name)
    return {**state, "video_url": url, "public_id": public_id_from_url(url),
            "hosting_deleted": False}


def _targets(client: BufferClient, services: list[str], state: dict, again: bool) -> list[dict]:
    channels = [c for c in client.all_channels()
                if c["service"] in services and not c.get("isDisconnected")]
    if not channels:
        raise PublishError(f"No connected Buffer channels for: {', '.join(services)}")
    return [c for c in channels if again or c["id"] not in state["posts"]]


def publish_project(cfg: Config, project_dir: Path, services: list[str], when: str,
                    draft: bool, dry_run: bool = False, again: bool = False) -> list[dict]:
    project_dir = project_dir.resolve()
    script = load_script(project_dir)
    if not (project_dir / "video.mp4").is_file():
        raise PublishError("No video.mp4 yet. Render the project first.")
    if unchecked_scenes(script):
        raise PublishError(f"Scenes {unchecked_scenes(script)} still contain [CHECK: ...]. "
                           "Verify the facts, edit script.json, re-render, then publish.")
    mode, due_at = parse_when(when, (cfg.publish or {}).get("timezone"))
    texts = build_texts(cfg, project_dir, script)
    state_path = project_dir / "publish.json"
    state = _load_state(state_path)
    client = BufferClient(cfg.buffer_key or "")
    targets = _targets(client, services, state, again)
    if not targets:
        print("  already sent to every selected channel (use --again to repost)")
        return []

    label = "draft" if draft else mode
    for channel in targets:
        print(f"  {channel['service']} · {channel['name']} · {label}")
    if dry_run:
        print("\n--- YouTube text ---\n" + texts["youtube"] + "\n\n--- Social text ---\n" + texts["social"])
        return []

    state = _host_video(cfg, project_dir, state)
    _save_state(state_path, state)
    results = []
    for channel in targets:
        post_input = build_post_input(channel["service"], channel["id"], script, texts,
                                      state["video_url"], mode, due_at, draft, cfg.publish or {})
        post = client.create_post(post_input)
        record = {"service": channel["service"], "post_id": post["id"],
                  "due_at": post.get("dueAt"), "draft": draft}
        state = {**state, "posts": {**state["posts"], channel["id"]: record}}
        _save_state(state_path, state)
        results.append(record)
    return results
