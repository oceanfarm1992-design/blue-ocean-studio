"""Free stock footage from the Pexels API, cached on disk."""
from __future__ import annotations

import re
from pathlib import Path

import httpx

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
RESULTS_PER_SEARCH = 15
REQUEST_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0
MIN_SHORT_SIDE_RATIO = 0.66  # accept files at least 2/3 of the target's short side
STEM_LENGTH = 4              # "filming" and "filmmaking" share the stem "film"
# Words too generic to prove a clip matches ("woman" alone would accept a glucose test)
GENERIC_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "with", "for", "by", "from",
    "video", "footage", "man", "men", "woman", "women", "person", "people", "guy", "girl",
    "boy", "young", "small", "business", "using", "doing", "holding", "looking",
}


class VisualsError(RuntimeError):
    pass


def pick_video_file(files: list[dict], width: int, height: int) -> dict | None:
    """Chooses the smallest MP4 that is still sharp enough for the target size."""
    target = min(width, height) * MIN_SHORT_SIDE_RATIO
    mp4s = [f for f in files
            if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")]
    if not mp4s:
        return None
    sharp = [f for f in mp4s if min(f["width"], f["height"]) >= target]
    if sharp:
        return min(sharp, key=lambda f: f["width"] * f["height"])
    return max(mp4s, key=lambda f: f["width"] * f["height"])


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return {w[:STEM_LENGTH] for w in words if len(w) >= 3 and w not in GENERIC_WORDS}


def is_relevant(query: str, video: dict) -> bool:
    """True when the clip's Pexels page title shares a meaningful word with the search.

    Pexels titles live in the page URL (".../video/woman-blow-drying-hair-123/"). A clip
    without a readable title is given the benefit of the doubt.
    """
    slug = str(video.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
    title = re.sub(r"-?\d+$", "", slug)
    wanted = _stems(query)
    if not wanted or not re.search(r"[a-z]", title):
        return True
    return bool(wanted & _stems(title.replace("-", " ")))


def search_pexels(api_key: str, query: str, orientation: str) -> list[dict]:
    try:
        response = httpx.get(
            PEXELS_SEARCH_URL,
            params={"query": query, "orientation": orientation, "per_page": RESULTS_PER_SEARCH},
            headers={"Authorization": api_key},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise VisualsError(f"Pexels search failed for '{query}': {exc}") from exc
    return response.json().get("videos", [])


def download(url: str, dest: Path) -> Path:
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(".part")
    try:
        with httpx.stream("GET", url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as response:
            response.raise_for_status()
            with partial.open("wb") as out:
                for block in response.iter_bytes():
                    out.write(block)
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise VisualsError(f"Download failed: {exc}") from exc
    partial.replace(dest)
    return dest


class ClipFinder:
    """Finds clips for a project without repeating the same footage."""

    def __init__(self, api_key: str | None, cache_dir: Path, size: tuple[int, int],
                 strict: bool = False):
        self.api_key = api_key
        self.strict = strict  # skip clips whose title does not match the search
        self.cache_dir = cache_dir
        self.size = size
        self.orientation = "portrait" if size[1] > size[0] else "landscape"
        self.used_ids: set[int] = set()
        self.credits: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def find(self, query: str, count: int) -> list[Path]:
        if not self.enabled or count <= 0:
            return []
        try:
            videos = search_pexels(self.api_key, query, self.orientation)
        except VisualsError as exc:
            print(f"  warning: {exc}")
            return []
        clips: list[Path] = []
        for video in videos:
            if len(clips) >= count:
                break
            if video.get("id") in self.used_ids:
                continue
            if self.strict and not is_relevant(query, video):
                continue
            clip = self._fetch(video)
            if clip is not None:
                clips.append(clip)
        return clips

    def _fetch(self, video: dict) -> Path | None:
        chosen = pick_video_file(video.get("video_files", []), *self.size)
        if chosen is None:
            return None
        dest = self.cache_dir / f"{video['id']}_{chosen.get('id', 'x')}.mp4"
        try:
            path = download(chosen["link"], dest)
        except VisualsError as exc:
            print(f"  warning: {exc}")
            return None
        self.used_ids.add(video["id"])
        author = video.get("user", {}).get("name", "Unknown")
        self.credits.append(f"Video by {author} on Pexels: {video.get('url', '')}")
        return path
