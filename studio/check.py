"""Read-only health check of every external service the pipeline uses."""
from __future__ import annotations

from collections.abc import Callable

import httpx

from studio.buffer import BufferClient
from studio.config import Config
from studio.hosting import parse_cloudinary_url
from studio.visuals import search_pexels
from studio.youtube import access_token

CHECK_TIMEOUT = 30.0
CLOUDINARY_FREE_CREDITS = 25


def _openai(cfg: Config) -> str:
    if not cfg.openai_key:
        raise RuntimeError("no key")
    from openai import OpenAI

    model = cfg.llm["model"]
    OpenAI(api_key=cfg.openai_key).models.retrieve(model)
    return f"key works, model {model} available"


def _pexels(cfg: Config) -> str:
    if not cfg.pexels_key:
        raise RuntimeError("no key")
    return f"key works ({len(search_pexels(cfg.pexels_key, 'small business', 'landscape'))} results)"


def _buffer(cfg: Config) -> str:
    channels = BufferClient(cfg.buffer_key or "").all_channels()
    wanted = set((cfg.publish or {}).get("services", [])) - {"youtube"}  # YouTube is direct
    found = {c["service"] for c in channels if not c.get("isDisconnected")}
    missing = wanted - found
    if missing:
        raise RuntimeError(f"not connected in Buffer: {', '.join(sorted(missing))}")
    return "connected: " + ", ".join(sorted(found & wanted))


def _cloudinary(cfg: Config) -> str:
    creds = parse_cloudinary_url(cfg.cloudinary_url)
    if creds is None:
        raise RuntimeError("CLOUDINARY_URL missing or not in cloudinary://key:secret@cloud format")
    response = httpx.get(f"https://api.cloudinary.com/v1_1/{creds.cloud_name}/usage",
                         auth=(creds.api_key, creds.api_secret), timeout=CHECK_TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Cloudinary rejected the credentials (HTTP {response.status_code})")
    used = response.json().get("credits", {}).get("usage", 0)
    return f"credentials work, {used:.2f} of {CLOUDINARY_FREE_CREDITS} free credits used this month"


def _youtube(cfg: Config) -> str:
    if cfg.youtube is None:
        raise RuntimeError("no YouTube login (run: python -m studio youtube-login)")
    access_token(cfg.youtube)
    return "login works (upload permission)"


CHECKS: tuple[tuple[str, Callable[[Config], str]], ...] = (
    ("OpenAI", _openai),
    ("Pexels", _pexels),
    ("Buffer", _buffer),
    ("Cloudinary", _cloudinary),
    ("YouTube", _youtube),
)


def run_checks(cfg: Config) -> list[tuple[str, bool, str]]:
    results = []
    for name, check in CHECKS:
        try:
            results.append((name, True, check(cfg)))
        except Exception as exc:  # report every failure instead of stopping at the first
            results.append((name, False, str(exc)[:200]))
    return results
