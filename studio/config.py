"""Loads channel settings and API keys."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from studio.youtube import YouTubeCredentials, load_credentials

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    root: Path
    channel: dict
    brand: dict
    llm: dict
    voice: dict
    video: dict
    openai_key: str | None
    pexels_key: str | None
    buffer_key: str | None = None
    cloudinary_url: str | None = None
    publish: dict | None = None
    youtube: YouTubeCredentials | None = None

    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"


def read_env_file(path: Path) -> dict[str, str]:
    """Parses KEY=VALUE lines. Missing file returns an empty dict."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config(root: Path = ROOT) -> Config:
    settings_path = root / "channel.toml"
    if not settings_path.is_file():
        raise FileNotFoundError(f"Missing settings file: {settings_path}")
    data = tomllib.loads(settings_path.read_text(encoding="utf-8"))

    # Later sources win: legacy .api.txt, .env.txt (Windows often adds .txt), .env, then
    # real environment variables.
    env = {
        **read_env_file(root / ".api.txt"),
        **read_env_file(root / ".env.txt"),
        **read_env_file(root / ".env"),
        **os.environ,
    }
    openai_key = env.get("OPENAI_API_KEY") or env.get("OPEN_AI_KEY") or None
    pexels_key = env.get("PEXELS_API_KEY") or None
    buffer_key = env.get("BUFFER_API_KEY") or env.get("Buffer_API") or env.get("BUFFER_API") or None
    cloudinary_url = env.get("CLOUDINARY_URL") or None

    return Config(
        root=root,
        channel=data["channel"],
        brand=data["brand"],
        llm=data["llm"],
        voice=data["voice"],
        video=data["video"],
        openai_key=openai_key,
        pexels_key=pexels_key,
        buffer_key=buffer_key,
        cloudinary_url=cloudinary_url,
        publish=data.get("publish", {}),
        youtube=load_credentials(root, env),
    )
