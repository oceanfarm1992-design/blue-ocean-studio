"""Asks the script model whether a stock clip's preview image really fits the scene.

Pexels search is keyword based, so "baker packing bag" also returns a woman packing a
suitcase. One low-detail image check with gpt-5-mini costs a fraction of a cent.
"""
from __future__ import annotations

from collections.abc import Callable

from studio.config import Config
from studio.llm import LLMError, complete_json

Checker = Callable[[str, dict], bool]

SYSTEM = (
    "You check stock footage for a marketing video. You see the preview frame of one clip "
    "and the scene it should illustrate. Answer match=true if a viewer would accept the frame "
    "as showing that scene. The right kind of business, person or place matters most; the "
    "exact action may differ when the clip clearly shows that business at work (a baker "
    "working in a bakery fits \"baker waving goodbye\"). A closely related business is fine "
    "(a cafe for a bakery, a barber for a salon). Unrelated settings, travel, families, "
    "offices for a shop scene, or only a loose keyword overlap (\"packing a suitcase\" for "
    "\"baker packing bag\") are match=false. Return only JSON: {\"match\": true or false}."
)


def make_checker(cfg: Config) -> Checker | None:
    """None when AI checks are off or there is no OpenAI key."""
    if not cfg.openai_key or not cfg.video.get("check_stock_with_ai", True):
        return None
    warned = False

    def check(query: str, video: dict) -> bool:
        nonlocal warned
        image = video.get("image")
        if not image:
            return True
        title = str(video.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
        try:
            answer = complete_json(cfg, SYSTEM, f"Scene: {query}\nClip title: {title}",
                                   image_url=image, quiet=True)
        except LLMError as exc:
            if not warned:
                print(f"  warning: footage check unavailable, using title match only: {exc}")
                warned = True
            return True  # the title filter already ran; never stop the build over this
        return answer.get("match") is True

    return check
