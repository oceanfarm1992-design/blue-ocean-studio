"""Uploads queued videos to YouTube shortly before their publish time.

YouTube's free API quota allows only a handful of uploads a day, so a week's 8 videos are
queued at build time and uploaded by the daily job when each is due within 48 hours.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from studio import youtube
from studio.cards import make_thumbnail
from studio.config import Config
from studio.media import extract_frame
from studio.posttext import build_texts, youtube_title
from studio.project import load_script
from studio.visuals import download

UPLOAD_WINDOW_HOURS = 48
MAX_UPLOADS_PER_RUN = 4       # stays inside YouTube's default daily quota
MISSED_GRACE_MINUTES = 5      # closer than this to its time, a video goes public immediately


class SyncError(RuntimeError):
    pass


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def is_due(publish_at: str | None, now: datetime, hours: int = UPLOAD_WINDOW_HOURS) -> bool:
    return publish_at is None or _parse(publish_at) - now <= timedelta(hours=hours)


def effective_schedule(publish_at: str | None, public_now: bool,
                       now: datetime) -> tuple[str | None, bool]:
    """A publish time that has (almost) passed becomes 'public now'."""
    if publish_at and _parse(publish_at) <= now + timedelta(minutes=MISSED_GRACE_MINUTES):
        return None, True
    return publish_at, public_now


def _video(project_dir: Path, state: dict) -> Path:
    video = project_dir / "video.mp4"
    if video.is_file():
        return video
    if not state.get("video_url") or state.get("hosting_deleted"):
        raise SyncError(f"{project_dir.name}: no video file and no hosted copy to download.")
    return download(state["video_url"], video)


def _thumbnail(cfg: Config, project_dir: Path, script: dict, video: Path) -> Path | None:
    thumb = project_dir / "thumbnail.jpg"
    if thumb.is_file():
        return thumb
    if script["format"] != "long":
        return None  # YouTube does not allow custom thumbnails on Shorts
    frame = project_dir / "build" / "thumb_bg.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    extract_frame(video, frame, 5.0)
    return make_thumbnail(script["thumbnail_text"], cfg.channel["name"], cfg.brand, thumb, frame)


def upload_pending(cfg: Config, project_dir: Path, now: datetime) -> dict | None:
    """Uploads this project's queued YouTube video if it is due. Returns the record."""
    state_path = project_dir / "publish.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pending = state.get("youtube_pending")
    if not pending or state.get("youtube_direct") or not is_due(pending.get("publish_at"), now):
        return None
    if cfg.youtube is None:
        raise SyncError("YouTube login missing. Run: python -m studio youtube-login")

    script = load_script(project_dir)
    publish_at, public_now = effective_schedule(pending.get("publish_at"),
                                                pending.get("public_now", False), now)
    settings = cfg.publish or {}
    metadata = youtube.build_metadata(
        youtube_title(script), build_texts(cfg, project_dir, script)["youtube"], script["tags"],
        str(settings.get("youtube_category", "27")), publish_at, public_now,
        bool(settings.get("disclose_ai", False)))
    video = _video(project_dir, state)
    video_id = youtube.upload(cfg.youtube, video, metadata)
    thumb = _thumbnail(cfg, project_dir, script, video)
    thumb_error = youtube.set_thumbnail(cfg.youtube, video_id, thumb) if thumb else None
    if thumb_error:
        print(f"  note: custom thumbnail not set ({thumb_error[:120]})")

    record = {"video_id": video_id, "url": f"https://youtu.be/{video_id}",
              "publish_at": publish_at, "public_now": public_now}
    updated = {k: v for k, v in state.items() if k != "youtube_pending"}
    state_path.write_text(json.dumps({**updated, "youtube_direct": record}, indent=2),
                          encoding="utf-8")
    return record


def sync(cfg: Config, now: datetime | None = None) -> list[tuple[str, dict]]:
    """Uploads every queued video that is due, up to the daily quota."""
    now = now or datetime.now(timezone.utc)
    uploaded: list[tuple[str, dict]] = []
    for state_path in sorted(cfg.projects_dir.rglob("publish.json")):
        if len(uploaded) >= MAX_UPLOADS_PER_RUN:
            print("  daily upload limit reached; the rest go tomorrow")
            break
        try:
            record = upload_pending(cfg, state_path.parent, now)
        except (SyncError, youtube.YouTubeError) as exc:
            print(f"  {state_path.parent.name}: {exc}")
            continue
        if record:
            uploaded.append((state_path.parent.name, record))
    return uploaded
