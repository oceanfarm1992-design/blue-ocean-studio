"""Removes hosted videos from Cloudinary once Buffer no longer needs them."""
from __future__ import annotations

import json
from pathlib import Path

from studio.buffer import BufferClient, BufferError
from studio.config import Config
from studio.hosting import HostingError, delete_video, parse_cloudinary_url, public_id_from_url

FINISHED = {"sent", None}  # None = the post was deleted in Buffer
PENDING = {"draft", "scheduled", "sending", "needs_approval"}


def cleanup_decision(statuses: list[str | None]) -> str:
    """'delete' when every post is sent or gone, 'error' if one failed, else 'keep'."""
    if not statuses:
        return "keep"
    if "error" in statuses:
        return "error"
    if all(status in FINISHED for status in statuses):
        return "delete"
    return "keep"


def find_published(projects_dir: Path) -> list[Path]:
    return sorted(projects_dir.rglob("publish.json"))


def _statuses(client: BufferClient, state: dict) -> list[str | None]:
    statuses = []
    for record in state.get("posts", {}).values():
        post = client.post(record["post_id"])
        statuses.append(post["status"] if post else None)
    return statuses


def cleanup(cfg: Config, dry_run: bool = False) -> dict[str, int]:
    creds = parse_cloudinary_url(cfg.cloudinary_url)
    if creds is None:
        raise HostingError("No valid CLOUDINARY_URL found in .env.txt.")
    client = BufferClient(cfg.buffer_key or "")
    summary = {"deleted": 0, "waiting": 0, "failed_posts": 0}
    for state_path in find_published(cfg.projects_dir):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        url = state.get("video_url")
        if not url or state.get("hosting_deleted"):
            continue
        name = state_path.parent.name
        if state.get("youtube_pending"):
            summary["waiting"] += 1  # the daily YouTube upload still needs this copy
            continue
        try:
            decision = cleanup_decision(_statuses(client, state))
        except BufferError as exc:
            print(f"  {name}: skipped, could not check Buffer ({exc})")
            continue
        if decision == "error":
            print(f"  {name}: a post failed in Buffer. Kept the video so you can retry.")
            summary["failed_posts"] += 1
        elif decision == "keep":
            summary["waiting"] += 1
        elif dry_run:
            print(f"  {name}: would delete hosted video")
            summary["deleted"] += 1
        else:
            public_id = state.get("public_id") or public_id_from_url(url)
            if public_id and delete_video(creds, public_id):
                updated = {**state, "hosting_deleted": True}
                state_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
                print(f"  {name}: deleted hosted video")
                summary["deleted"] += 1
    return summary
