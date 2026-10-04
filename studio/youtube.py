"""Direct YouTube uploads for long videos (Buffer only posts YouTube Shorts).

Locally the app reads client_secret.json and youtube_token.json; in GitHub Actions it reads
YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN secrets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
UPLOAD_TIMEOUT = 900.0
LOGIN_TIMEOUT_SECONDS = 600
TITLE_MAX = 100
DESCRIPTION_MAX = 4900
TAGS_MAX_CHARS = 450  # YouTube caps all tags together at 500 characters


class YouTubeError(RuntimeError):
    pass


@dataclass(frozen=True)
class YouTubeCredentials:
    client_id: str
    client_secret: str
    refresh_token: str


def load_credentials(root: Path, env: dict[str, str]) -> YouTubeCredentials | None:
    """From environment variables first, then local client_secret.json + youtube_token.json."""
    if env.get("YOUTUBE_REFRESH_TOKEN"):
        if not (env.get("YOUTUBE_CLIENT_ID") and env.get("YOUTUBE_CLIENT_SECRET")):
            return None
        return YouTubeCredentials(env["YOUTUBE_CLIENT_ID"], env["YOUTUBE_CLIENT_SECRET"],
                                  env["YOUTUBE_REFRESH_TOKEN"])
    client_file, token_file = root / "client_secret.json", root / "youtube_token.json"
    if not (client_file.is_file() and token_file.is_file()):
        return None
    client = next(iter(json.loads(client_file.read_text(encoding="utf-8")).values()))
    token = json.loads(token_file.read_text(encoding="utf-8"))
    if not token.get("refresh_token"):
        return None
    return YouTubeCredentials(client["client_id"], client["client_secret"], token["refresh_token"])


def login(root: Path) -> Path:
    """One-time browser sign-in. Saves the refresh token and a youtube.env for GitHub secrets."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_file = root / "client_secret.json"
    if not client_file.is_file():
        raise YouTubeError("client_secret.json not found in the project folder.")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=[SCOPE])
    try:
        creds = flow.run_local_server(
            port=0, access_type="offline", prompt="consent", open_browser=True,
            timeout_seconds=LOGIN_TIMEOUT_SECONDS,
            authorization_prompt_message="If no browser opened, open this link:\n{url}\n")
    except Exception as exc:  # the library raises several unrelated error types
        raise YouTubeError(f"Sign-in did not finish ({exc}). Run the command again.") from exc
    if not creds.refresh_token:
        raise YouTubeError("Google did not return a refresh token. Run the login again.")
    (root / "youtube_token.json").write_text(
        json.dumps({"refresh_token": creds.refresh_token}), encoding="utf-8")
    client = next(iter(json.loads(client_file.read_text(encoding="utf-8")).values()))
    env_file = root / "youtube.env"
    env_file.write_text(
        f"YOUTUBE_CLIENT_ID={client['client_id']}\n"
        f"YOUTUBE_CLIENT_SECRET={client['client_secret']}\n"
        f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}\n", encoding="utf-8")
    return env_file


def access_token(creds: YouTubeCredentials) -> str:
    try:
        response = httpx.post(TOKEN_URL, data={
            "client_id": creds.client_id, "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token, "grant_type": "refresh_token",
        }, timeout=30.0)
    except httpx.HTTPError as exc:
        raise YouTubeError(f"Could not reach Google: {exc}") from exc
    body = response.json() if response.content else {}
    if response.status_code != 200 or "access_token" not in body:
        reason = body.get("error_description") or body.get("error") or response.text[:200]
        raise YouTubeError(f"Google refused the YouTube login ({reason}). Run: python -m studio "
                           "youtube-login, then update the GitHub secrets.")
    return body["access_token"]


def trim_tags(tags: list[str], limit: int = TAGS_MAX_CHARS) -> list[str]:
    kept, used = [], 0
    for tag in tags:
        cost = len(tag) + (2 if " " in tag else 0) + 1
        if used + cost > limit:
            break
        kept.append(tag)
        used += cost
    return kept


def build_metadata(title: str, description: str, tags: list[str], category: str,
                   publish_at: str | None, public_now: bool, disclose_ai: bool) -> dict:
    """Video resource for the upload. With publish_at, YouTube publishes it at that time."""
    status = {"selfDeclaredMadeForKids": False, "containsSyntheticMedia": disclose_ai}
    if publish_at:
        status.update(privacyStatus="private", publishAt=publish_at)
    else:
        status["privacyStatus"] = "public" if public_now else "private"
    return {
        "snippet": {"title": title[:TITLE_MAX], "description": description[:DESCRIPTION_MAX],
                    "tags": trim_tags(tags), "categoryId": category},
        "status": status,
    }


def upload(creds: YouTubeCredentials, video: Path, metadata: dict) -> str:
    """Resumable upload. Returns the new video's ID."""
    token = access_token(creds)
    headers = {"Authorization": f"Bearer {token}"}
    size = video.stat().st_size
    try:
        start = httpx.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                           headers={**headers, "X-Upload-Content-Type": "video/mp4",
                                    "X-Upload-Content-Length": str(size)},
                           json=metadata, timeout=60.0)
        if start.status_code != 200 or "location" not in start.headers:
            raise YouTubeError(f"YouTube refused the upload: {start.text[:400]}")
        with video.open("rb") as handle:
            done = httpx.put(start.headers["location"], content=handle,
                             headers={**headers, "Content-Type": "video/mp4",
                                      "Content-Length": str(size)},
                             timeout=UPLOAD_TIMEOUT)
    except httpx.HTTPError as exc:
        raise YouTubeError(f"YouTube upload failed: {exc}") from exc
    if done.status_code not in (200, 201):
        raise YouTubeError(f"YouTube upload failed: {done.text[:400]}")
    return done.json()["id"]


def set_thumbnail(creds: YouTubeCredentials, video_id: str, image: Path) -> str | None:
    """Sets a custom thumbnail. Returns an error message instead of raising (it is optional)."""
    try:
        response = httpx.post(THUMBNAIL_URL, params={"videoId": video_id},
                              headers={"Authorization": f"Bearer {access_token(creds)}",
                                       "Content-Type": "image/jpeg"},
                              content=image.read_bytes(), timeout=120.0)
    except (httpx.HTTPError, YouTubeError) as exc:
        return str(exc)
    if response.status_code != 200:
        return response.text[:300]
    return None
