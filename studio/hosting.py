"""Hosts finished videos on the user's Cloudinary account so Buffer can fetch them by URL."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from studio.media import probe_duration, run

UPLOAD_TIMEOUT = 600.0
FREE_PLAN_MAX_BYTES = 95 * 1024 * 1024  # Cloudinary free plan caps videos at 100 MB
AUDIO_KBPS = 128
MIN_VIDEO_KBPS = 500
UPLOAD_FOLDER = "blue-ocean-marketing"


class HostingError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloudinaryCredentials:
    cloud_name: str
    api_key: str
    api_secret: str


def parse_cloudinary_url(url: str | None) -> CloudinaryCredentials | None:
    """Parses cloudinary://API_KEY:API_SECRET@CLOUD_NAME, the format Cloudinary's dashboard shows."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme != "cloudinary" or not (parsed.username and parsed.password and parsed.hostname):
        return None
    return CloudinaryCredentials(parsed.hostname, parsed.username, parsed.password)


def sign(params: dict[str, str], api_secret: str) -> str:
    """Cloudinary signature: sorted key=value pairs joined by '&', then the secret, SHA-1."""
    payload = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return hashlib.sha1((payload + api_secret).encode("utf-8")).hexdigest()


def fit_under_limit(video: Path, work_dir: Path, max_bytes: int = FREE_PLAN_MAX_BYTES) -> Path:
    """Returns the video itself, or a re-encoded copy small enough for the free plan."""
    if video.stat().st_size <= max_bytes:
        return video
    duration = probe_duration(video)
    video_kbps = int(max_bytes * 8 / 1000 / duration * 0.95) - AUDIO_KBPS
    if video_kbps < MIN_VIDEO_KBPS:
        raise HostingError(f"{video.name} is too long to fit the free hosting limit.")
    out = work_dir / "upload.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{video_kbps}k",
         "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
         "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-movflags", "+faststart", str(out)])
    return out


def public_id_from_url(url: str) -> str | None:
    """'https://res.cloudinary.com/c/video/upload/v123/folder/name.mp4' -> 'folder/name'."""
    path = urlparse(url).path
    marker = "/upload/"
    if marker not in path:
        return None
    tail = path.split(marker, 1)[1]
    parts = tail.split("/")
    if parts and parts[0].startswith("v") and parts[0][1:].isdigit():
        parts = parts[1:]
    joined = "/".join(parts)
    return joined.rsplit(".", 1)[0] or None


def delete_video(creds: CloudinaryCredentials, public_id: str) -> bool:
    """Deletes a hosted video. True if it was deleted or was already gone."""
    params = {"public_id": public_id, "timestamp": str(int(time.time()))}
    data = {**params, "api_key": creds.api_key, "signature": sign(params, creds.api_secret)}
    endpoint = f"https://api.cloudinary.com/v1_1/{creds.cloud_name}/video/destroy"
    try:
        response = httpx.post(endpoint, data=data, timeout=60.0)
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HostingError(f"Could not delete {public_id} from Cloudinary: {exc}") from exc
    if response.status_code != 200:
        reason = body.get("error", {}).get("message", response.text[:200])
        raise HostingError(f"Cloudinary refused to delete {public_id}: {reason}")
    return body.get("result") in ("ok", "not found")


def upload_video(creds: CloudinaryCredentials, video: Path, public_id: str) -> str:
    params = {"folder": UPLOAD_FOLDER, "overwrite": "true", "public_id": public_id,
              "timestamp": str(int(time.time()))}
    data = {**params, "api_key": creds.api_key, "signature": sign(params, creds.api_secret)}
    endpoint = f"https://api.cloudinary.com/v1_1/{creds.cloud_name}/video/upload"
    try:
        with video.open("rb") as handle:
            response = httpx.post(endpoint, data=data,
                                  files={"file": (video.name, handle, "video/mp4")},
                                  timeout=UPLOAD_TIMEOUT)
    except httpx.HTTPError as exc:
        raise HostingError(f"Upload to Cloudinary failed: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or "secure_url" not in body:
        reason = body.get("error", {}).get("message") or response.text[:300]
        raise HostingError(f"Cloudinary rejected the upload: {reason}")
    return body["secure_url"]
