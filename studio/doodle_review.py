"""One cheap AI look at a finished doodle before it goes in a video.

Low-quality drawings sometimes contain scribbled writing ("909" on a board) or a smeared
face. Pixel clean-up cannot fix content, so a bad drawing is redrawn once.
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from studio.config import Config
from studio.llm import LLMError, complete_json

PREVIEW_SIZE = 1024  # big enough to see a smeared face; still well under $0.001 a check

SYSTEM = (
    "You check AI whiteboard doodles before they appear in a marketing video. Reject "
    "(ok=false) a drawing that contains any letters, numbers, words or scribbled writing; a "
    "distorted, melted or smeared face, hand or object; or a messy, garbled area. Clean, "
    "simple line art with an empty board or blank sign is ok. Return only JSON: "
    "{\"ok\": true or false, \"problem\": \"short reason, empty when ok\"}"
)


def _data_url(image: Image.Image) -> str:
    preview = image.convert("RGB")
    preview.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
    buffer = io.BytesIO()
    preview.save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def review(cfg: Config, image: Image.Image, prompt: str) -> tuple[bool, str]:
    """(ok, problem). If the check itself fails the drawing is accepted: never stop a build."""
    try:
        answer = complete_json(cfg, SYSTEM, f"The drawing should show: {prompt}",
                               image_url=_data_url(image), quiet=True, detail="high")
    except LLMError as exc:
        return True, f"review unavailable: {exc}"
    ok = answer.get("ok") is not False
    return ok, "" if ok else str(answer.get("problem", "")).strip() or "rejected"
