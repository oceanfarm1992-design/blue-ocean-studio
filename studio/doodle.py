"""Whiteboard doodle scenes: an AI line drawing revealed by a hand holding a marker.

The drawing comes from the OpenAI image API (cached per prompt, so a cut Short reuses the
long video's drawing for free). The reveal follows the ink cell by cell in nearest-neighbour
order, so the marker tip appears to trace the lines, then the hand slides away.
"""
from __future__ import annotations

import base64
import hashlib
import io
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from studio.config import Config
from studio.media import AUDIO_BITRATE, AUDIO_RATE, BT709_FLAGS, VIDEO_CRF, MediaError

HAND_PATH = Path(__file__).resolve().parent / "assets" / "hand.png"
STYLE = (
    "Whiteboard doodle illustration, hand-drawn black marker line art on a pure white "
    "background, simple clean outlines, no shading, no gradients, minimal, one small accent "
    "of blue, lots of empty white space, no text, no letters, no numbers."
)
MODES = ("off", "mix", "all")
DEFAULTS = {
    "mode": "mix",
    "image_model": "gpt-image-1",
    "quality": "medium",
    "min_scene_seconds": 3.0,
    "hold_seconds": 0.2,
    "max_draw_seconds": 10.0,
    "replace_weak_stock": True,
}
CELL = 14                 # reveal grid in pixels
INK_THRESHOLD = 215       # grey levels below this count as ink
EXIT_SECONDS = 0.6        # hand slides off after drawing
MIN_DRAW_SECONDS = 0.5
DRAW_SHARE = 0.75         # drawing takes the first 75% of the spoken line
NEAR_WHITE = 235          # AI "white" backgrounds are slightly grey; snap them to pure white
FFMPEG_TIMEOUT = 600      # seconds to finish encoding after the last frame
HAND_SCALE = 0.32         # hand width as a share of the frame's longer side
BOX_WIDTH = 0.89          # drawing area as a share of the frame
BOX_HEIGHT_PORTRAIT = 0.75  # leaves the bottom free for captions
BOX_HEIGHT_LANDSCAPE = 0.80
TOP_MARGIN = 0.045


class DoodleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Timing:
    total: float   # whole scene, including the hold on the finished drawing
    draw: float    # seconds spent drawing


def settings(cfg: Config) -> dict:
    merged = {**DEFAULTS, **(cfg.doodle or {})}
    if merged["mode"] not in MODES:
        raise DoodleError(f"Unknown doodle mode '{merged['mode']}'. Use one of: {', '.join(MODES)}.")
    for name in ("min_scene_seconds", "hold_seconds", "max_draw_seconds"):
        if float(merged[name]) < 0:
            raise DoodleError(f"[doodle] {name} cannot be negative.")
    return merged


def available(cfg: Config) -> bool:
    return settings(cfg)["mode"] != "off" and bool(cfg.openai_key)


def wants_doodle(cfg: Config, scene: dict) -> bool:
    if not available(cfg):
        return False
    return settings(cfg)["mode"] == "all" or scene.get("visual") == "doodle"


def replaces_weak_stock(cfg: Config) -> bool:
    return available(cfg) and bool(settings(cfg)["replace_weak_stock"])


def scene_prompt(scene: dict) -> str:
    return scene.get("doodle_prompt") or scene["visual_query"]


def timing(cfg: Config, spoken_seconds: float) -> Timing:
    """The scene follows the voice like a stock scene: the drawing finishes while the line
    is still being spoken, so there is no silent wait. Very short lines get a minimum length
    so the drawing is not rushed."""
    s = settings(cfg)
    total = max(spoken_seconds + float(s["hold_seconds"]), float(s["min_scene_seconds"]),
                MIN_DRAW_SECONDS + EXIT_SECONDS)
    draw = min(spoken_seconds * DRAW_SHARE, float(s["max_draw_seconds"]), total - EXIT_SECONDS)
    return Timing(round(total, 3), round(max(draw, MIN_DRAW_SECONDS), 3))


# ---------- drawings ----------

def _cache_key(s: dict, prompt: str) -> str:
    raw = f"{s['image_model']}|{s['quality']}|{STYLE}|{prompt.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _orientation(size: tuple[int, int]) -> str:
    return "portrait" if size[1] > size[0] else "landscape"


def _is_image(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return False


def _valid_cached(path: Path) -> bool:
    """A cached drawing that no longer opens is deleted so it gets drawn again."""
    if _is_image(path.read_bytes()):
        return True
    path.unlink(missing_ok=True)
    return False


def doodle_image(cfg: Config, prompt: str, size: tuple[int, int], cache_dir: Path) -> Path:
    """Cached drawing for this prompt. A Short cut from a long video reuses the long video's
    landscape drawing (centred, smaller) instead of paying for a second one."""
    s = settings(cfg)
    key = _cache_key(s, prompt)
    wanted = cache_dir / f"{key}_{_orientation(size)}.png"
    candidates = [wanted, *sorted(p for p in cache_dir.glob(f"{key}_*.png") if p != wanted)]
    for path in candidates:
        if path.is_file() and _valid_cached(path):
            return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = _generate(cfg, s, f"{prompt.rstrip('.')}. {STYLE}", size)
    if not _is_image(data):
        raise DoodleError("Doodle image API returned data that is not an image.")
    partial = wanted.with_suffix(".part")
    partial.write_bytes(data)
    partial.replace(wanted)
    return wanted


def _generate(cfg: Config, s: dict, prompt: str, size: tuple[int, int]) -> bytes:
    if not cfg.openai_key:
        raise DoodleError("Doodles need an OpenAI key (OPENAI_API_KEY).")
    from openai import OpenAI, OpenAIError

    api_size = "1024x1536" if _orientation(size) == "portrait" else "1536x1024"
    try:
        result = OpenAI(api_key=cfg.openai_key).images.generate(
            model=s["image_model"], prompt=prompt, size=api_size, quality=s["quality"], n=1)
        return base64.b64decode(result.data[0].b64_json)
    except (OpenAIError, IndexError, TypeError, ValueError) as exc:
        raise DoodleError(f"Doodle image failed: {exc}") from exc


# ---------- layout ----------

def fit_box(image_size: tuple[int, int], frame: tuple[int, int]) -> tuple[int, int, int, int]:
    """(width, height, x, y) of the drawing: centred in the area above the captions."""
    fw, fh = frame
    box_w = fw * BOX_WIDTH
    box_h = fh * (BOX_HEIGHT_PORTRAIT if fh > fw else BOX_HEIGHT_LANDSCAPE)
    scale = min(box_w / image_size[0], box_h / image_size[1])
    w, h = round(image_size[0] * scale), round(image_size[1] * scale)
    return w, h, (fw - w) // 2, round(fh * TOP_MARGIN + (box_h - h) / 2)


def compose(image_path: Path, frame: tuple[int, int]) -> np.ndarray:
    """The finished drawing on a white frame, as an RGB array."""
    image = Image.open(image_path).convert("RGB")
    w, h, x, y = fit_box(image.size, frame)
    canvas = Image.new("RGB", frame, "white")
    canvas.paste(image.resize((w, h), Image.LANCZOS), (x, y))
    art = np.asarray(canvas).copy()
    art[art.min(axis=2) > NEAR_WHITE] = 255
    return art


def stroke_order(art: np.ndarray, cell: int = CELL) -> list[tuple[int, int]]:
    """Ink cells (row, col) in greedy nearest-neighbour order, so the pen follows lines."""
    gray = art.mean(axis=2)
    rows, cols = gray.shape[0] // cell, gray.shape[1] // cell
    blocks = gray[: rows * cell, : cols * cell].reshape(rows, cell, cols, cell).min(axis=(1, 3))
    cells = np.argwhere(blocks < INK_THRESHOLD)
    if len(cells) == 0:
        return []
    remaining = np.ones(len(cells), dtype=bool)
    current = int(np.lexsort((cells[:, 1], cells[:, 0]))[0])  # top-left first
    order = []
    while True:
        remaining[current] = False
        order.append((int(cells[current, 0]), int(cells[current, 1])))
        if not remaining.any():
            return order
        distance = np.abs(cells - cells[current]).sum(axis=1).astype(float)
        distance[~remaining] = np.inf
        current = int(np.argmin(distance))


# ---------- hand ----------

def find_tip(alpha: np.ndarray) -> tuple[int, int]:
    """Marker tip = the most upper-left opaque pixel of the hand image."""
    ys, xs = np.nonzero(alpha > 128)
    if len(xs) == 0:
        raise DoodleError("Hand image is fully transparent.")
    i = int(np.argmin(xs + ys))
    return int(xs[i]), int(ys[i])


def load_hand(frame: tuple[int, int], path: Path = HAND_PATH) -> tuple[Image.Image, tuple[int, int]]:
    if not path.is_file():
        raise DoodleError(f"Hand image missing: {path}")
    hand = Image.open(path).convert("RGBA")
    width = round(max(frame) * HAND_SCALE)
    hand = hand.resize((width, round(hand.height * width / hand.width)), Image.LANCZOS)
    return hand, find_tip(np.asarray(hand)[:, :, 3])


# ---------- animation ----------

def _with_hand(frame: np.ndarray, hand: Image.Image, tip: tuple[int, int], at: tuple[int, int]) -> bytes:
    image = Image.fromarray(frame)
    image.paste(hand, (at[0] - tip[0], at[1] - tip[1]), hand)
    return image.tobytes()


def frames(art: np.ndarray, hand: Image.Image, tip: tuple[int, int], total_frames: int,
           draw_frames: int, exit_frames: int) -> Iterator[bytes]:
    """Raw RGB frames: draw, slide the hand out towards the bottom right, then hold."""
    order = stroke_order(art)
    per_frame = max(1, -(-len(order) // max(1, draw_frames)))
    height, width = art.shape[:2]
    canvas = np.full_like(art, 255)
    pen = (width // 2, height // 2)
    shown = exited = 0
    for _ in range(total_frames):
        batch = order[shown:shown + per_frame]
        shown += len(batch)
        if batch:
            for r, c in batch:
                canvas[r * CELL:(r + 1) * CELL, c * CELL:(c + 1) * CELL] = \
                    art[r * CELL:(r + 1) * CELL, c * CELL:(c + 1) * CELL]
            r, c = batch[-1]
            pen = (c * CELL + CELL // 2, r * CELL + CELL // 2)
            yield _with_hand(canvas, hand, tip, pen)
            continue
        canvas = art  # exact finished drawing, including any light pixels skipped above
        if exited < exit_frames:
            exited += 1
            yield _exit_frame(canvas, hand, tip, pen, exited / exit_frames)
        else:
            yield canvas.tobytes()


def _exit_frame(canvas: np.ndarray, hand: Image.Image, tip: tuple[int, int],
                pen: tuple[int, int], progress: float) -> bytes:
    height, width = canvas.shape[:2]
    ease = progress * progress
    at = (int(pen[0] + ease * (width - pen[0] + hand.width)),
          int(pen[1] + ease * (height - pen[1] + hand.height)))
    return _with_hand(canvas, hand, tip, at)


def build_doodle_cmd(audio: Path, size: tuple[int, int], fps: int, duration: float,
                     subs_name: str, out: Path) -> list[str]:
    """FFmpeg reading raw frames on stdin; encoding matches media.build_scene_cmd so the
    finished scenes can be joined without re-encoding."""
    width, height = size
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-", "-i", str(audio),
        "-filter_complex",
        f"[0:v]scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setsar=1,"
        f"subtitles={subs_name}[vout];"
        f"[1:a]apad=whole_dur={duration:.3f},aresample={AUDIO_RATE}[aout]",
        "-map", "[vout]", "-map", "[aout]", "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", VIDEO_CRF, "-r", str(fps),
        *BT709_FLAGS, "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2", str(out),
    ]


def render_scene(image: Path, audio: Path, scene_timing: Timing, size: tuple[int, int],
                 fps: int, subs_name: str, out: Path, cwd: Path) -> Path:
    art = compose(image, size)
    hand, tip = load_hand(size)
    total_frames = round(scene_timing.total * fps)
    draw_frames = round(scene_timing.draw * fps)
    cmd = build_doodle_cmd(audio, size, fps, total_frames / fps, subs_name, out)
    scene_frames = frames(art, hand, tip, total_frames, draw_frames, round(EXIT_SECONDS * fps))
    # FFmpeg's messages go to a file: a pipe nobody reads can fill up and freeze both sides.
    with tempfile.TemporaryFile() as log:
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stderr=log)
        except FileNotFoundError as exc:
            raise MediaError("FFmpeg not found. Install it and make sure it is on PATH.") from exc
        _feed(proc, scene_frames)
        log.seek(0)
        message = log.read().decode(errors="replace").strip()
    if proc.returncode != 0:
        raise MediaError(f"FFmpeg failed on doodle scene {out}:\n{message}")
    return out


def _feed(proc: subprocess.Popen, scene_frames: Iterator[bytes]) -> None:
    """Writes every frame, then waits; never leaves FFmpeg running if anything goes wrong."""
    try:
        try:
            for frame in scene_frames:
                proc.stdin.write(frame)
        except BrokenPipeError:
            pass  # FFmpeg stopped early; its exit code and log explain why
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
        proc.wait(timeout=FFMPEG_TIMEOUT)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
