"""Publishes a rendered project: YouTube through its own API, other networks through Buffer.

Buffer posts Facebook, LinkedIn and Instagram from a hosted copy of the video. YouTube
uploads are queued and sent by studio.ytsync close to their publish time, because YouTube's
free API quota only allows a few uploads a day.
"""
from __future__ import annotations

import json
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
from studio.posttext import (  # noqa: F401  (re-exported for callers and tests)
    build_texts,
    hashtags,
    social_text,
    youtube_text,
    youtube_title,
)
from studio.project import load_script
from studio.script import unchecked_scenes
from studio.ytsync import upload_pending

SUPPORTED_SERVICES = ("youtube", "facebook", "linkedin", "instagram", "tiktok")
VERTICAL_ONLY_SERVICES = {"instagram", "tiktok"}  # Reels-style networks skip landscape videos


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


def youtube_privacy(mode: str, due_at: str | None, draft: bool) -> tuple[str | None, bool]:
    """(publishAt, public right away) for a direct YouTube upload."""
    if draft:
        return None, False
    if mode == "customScheduled" and due_at:
        return due_at, False
    return None, True


def buffer_services_for(script: dict, services: list[str]) -> list[str]:
    """Services that go through Buffer for this video (YouTube never does)."""
    is_long = script["format"] == "long"
    return [s for s in services
            if s != "youtube" and not (is_long and s in VERTICAL_ONLY_SERVICES)]


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
    if not services:
        return []
    channels = [c for c in client.all_channels()
                if c["service"] in services and not c.get("isDisconnected")]
    if not channels:
        raise PublishError(f"No connected Buffer channels for: {', '.join(services)}")
    return [c for c in channels if again or c["id"] not in state["posts"]]


def _wants_youtube(services: list[str], state: dict, again: bool) -> bool:
    queued = state.get("youtube_direct") or state.get("youtube_pending")
    return "youtube" in services and (again or not queued)


def _queue_youtube(cfg: Config, project_dir: Path, state_path: Path, state: dict, mode: str,
                   due_at: str | None, draft: bool) -> dict:
    publish_at, public_now = youtube_privacy(mode, due_at, draft)
    queued = {k: v for k, v in state.items() if k != "youtube_direct"}
    _save_state(state_path, {**queued, "youtube_pending": {
        "publish_at": publish_at, "public_now": public_now, "draft": draft}})
    record = upload_pending(cfg, project_dir, datetime.now(timezone.utc))
    if record:
        return {"service": "youtube", "post_id": record["video_id"],
                "due_at": record["publish_at"], "draft": draft, "url": record["url"]}
    return {"service": "youtube", "post_id": None, "due_at": publish_at, "draft": draft,
            "queued": True}


def _validate(project_dir: Path, script: dict, dry_run: bool) -> None:
    if not (project_dir / "video.mp4").is_file():
        raise PublishError("No video.mp4 yet. Render the project first.")
    pending = unchecked_scenes(script)
    if not pending:
        return
    message = (f"Scenes {pending} still contain [CHECK: ...]. "
               "Verify the facts, edit script.json, re-render, then publish.")
    if not dry_run:
        raise PublishError(message)
    print(f"  WARNING (a real run would stop here): {message}")


def publish_project(cfg: Config, project_dir: Path, services: list[str], when: str,
                    draft: bool, dry_run: bool = False, again: bool = False) -> list[dict]:
    project_dir = project_dir.resolve()
    script = load_script(project_dir)
    _validate(project_dir, script, dry_run)
    mode, due_at = parse_when(when, (cfg.publish or {}).get("timezone"))
    texts = build_texts(cfg, project_dir, script)
    state_path = project_dir / "publish.json"
    state = _load_state(state_path)
    client = BufferClient(cfg.buffer_key or "")
    targets = _targets(client, buffer_services_for(script, services), state, again)
    wants_youtube = _wants_youtube(services, state, again)
    if not targets and not wants_youtube:
        print("  already sent to every selected channel (use --again to repost)")
        return []

    label = "draft" if draft else mode
    if wants_youtube:
        print(f"  youtube · direct upload · {'private' if draft else label}")
    for channel in targets:
        print(f"  {channel['service']} · {channel['name']} · {label}")
    if dry_run:
        print("\n--- YouTube text ---\n" + texts["youtube"] + "\n\n--- Social text ---\n" + texts["social"])
        return []

    # Host first: Buffer needs the URL, and queued YouTube uploads download from it later.
    state = _host_video(cfg, project_dir, state)
    _save_state(state_path, state)
    results = []
    if wants_youtube:
        results.append(_queue_youtube(cfg, project_dir, state_path, state, mode, due_at, draft))
        state = _load_state(state_path)
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
