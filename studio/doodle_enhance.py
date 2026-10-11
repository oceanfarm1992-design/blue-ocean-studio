"""Turns a cheap, soft AI doodle into crisp marker line art.

Low-quality image generation is 4x cheaper but gives blurry edges, grey haze and smudges,
and a drifting accent colour. Line art is simple enough to rebuild: work at 2x size,
turn darkness into a clean anti-aliased ink mask, drop specks and grey haze, and repaint
any coloured area in one consistent accent colour.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

UPSCALE = 2
BLUR_RADIUS = 1.2         # smooths jagged, blocky edges before thresholding (at 2x)
INK_LOW = 0.38            # darkness below this is paper (removes grey haze and smudges)
INK_HIGH = 0.58           # darkness above this is solid ink; between = anti-aliased edge
SPECK_SIZE = 5            # ink blobs thinner than this many pixels (at 2x) are noise
COLOR_SATURATION = 0.30   # pixels this saturated (and not dark) belong to the accent fill
COLOR_MIN_VALUE = 0.35
COLOR_SMOOTH = 5          # median filter that turns blotchy colour into flat fills
INK = np.array([22, 22, 26], dtype=np.float32)      # marker black, slightly soft
ACCENT = np.array([66, 135, 220], dtype=np.float32)  # one blue for every drawing


def _smoothstep(x: np.ndarray, low: float, high: float) -> np.ndarray:
    t = np.clip((x - low) / (high - low), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _colored(image: Image.Image) -> np.ndarray:
    """True where a pixel is part of the coloured accent (saturated and not dark)."""
    hsv = np.asarray(image.convert("HSV"), dtype=np.float32) / 255.0
    return (hsv[:, :, 1] > COLOR_SATURATION) & (hsv[:, :, 2] > COLOR_MIN_VALUE)


def _ink_alpha(image: Image.Image, colored: np.ndarray) -> np.ndarray:
    """0..1 ink coverage per pixel: crisp, anti-aliased, without haze or specks.
    Coloured areas are never ink, however dark the blue (their outlines still are)."""
    gray_array = np.asarray(image.convert("L")).copy()
    gray_array[colored] = 255
    gray = Image.fromarray(gray_array).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    darkness = 1.0 - np.asarray(gray, dtype=np.float32) / 255.0
    alpha = _smoothstep(darkness, INK_LOW, INK_HIGH)
    solid = Image.fromarray(((alpha > 0.5) * 255).astype(np.uint8))
    kept = solid.filter(ImageFilter.MinFilter(SPECK_SIZE)).filter(ImageFilter.MaxFilter(SPECK_SIZE + 2))
    return alpha * (np.asarray(kept, dtype=np.float32) / 255.0)


def _accent_mask(colored: np.ndarray) -> np.ndarray:
    """0..1 where the drawing has its coloured accent, as a flat, clean shape."""
    mask = Image.fromarray((colored * 255).astype(np.uint8)).filter(
        ImageFilter.MedianFilter(COLOR_SMOOTH)).filter(ImageFilter.GaussianBlur(1))
    return np.asarray(mask, dtype=np.float32) / 255.0


def enhance(image: Image.Image) -> Image.Image:
    """Same size as the input; pure white paper, solid ink lines, one flat accent colour."""
    original_size = image.size
    big = image.convert("RGB").resize(
        (original_size[0] * UPSCALE, original_size[1] * UPSCALE), Image.LANCZOS)
    colored = _colored(big)
    alpha = _ink_alpha(big, colored)[:, :, None]
    accent = _accent_mask(colored)[:, :, None]
    paper = np.full(alpha.shape[:2] + (3,), 255.0, dtype=np.float32)
    painted = paper * (1 - accent) + ACCENT * accent
    result = painted * (1 - alpha) + INK * alpha
    out = Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))
    return out.resize(original_size, Image.LANCZOS)
