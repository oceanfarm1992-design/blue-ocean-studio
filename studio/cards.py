"""Branded text cards (fallback visuals) and YouTube thumbnails, drawn with Pillow."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

THUMB_SIZE = (1280, 720)
THUMB_DARKEN = 0.45
CARD_TEXT_CENTER = 0.36  # vertical centre of card text, as a fraction of height


# Used when the configured (Windows) font is missing, e.g. on a Linux cloud runner.
FALLBACK_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (path, *FALLBACK_FONTS):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def fit_text(draw, text: str, font_path: str, max_width: int, max_height: int,
             start_size: int, min_size: int = 24) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font size at which the wrapped text fits the box."""
    size = start_size
    while True:
        font = load_font(font_path, size)
        lines = wrap_text(draw, text, font, max_width)
        line_height = int(size * 1.15)
        if len(lines) * line_height <= max_height or size <= min_size:
            return font, lines
        size -= 4


def make_card(text: str, size: tuple[int, int], brand: dict, out: Path) -> Path:
    width, height = size
    image = Image.new("RGB", size, brand["dark"])
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.1)
    font, lines = fit_text(draw, text, brand["font_bold"], width - 2 * margin,
                           int(height * 0.5), start_size=int(min(width, height) * 0.11))
    line_height = int(font.size * 1.15)
    # Centre the text block a little above the middle so it stays clear of captions.
    top = int(height * CARD_TEXT_CENTER) - (line_height * len(lines)) // 2
    for i, line in enumerate(lines):
        line_width = draw.textlength(line, font=font)
        draw.text(((width - line_width) / 2, top + i * line_height), line,
                  font=font, fill=brand["light"])
    bar_width = int(width * 0.12)
    bar_top = top + line_height * len(lines) + int(height * 0.03)
    draw.rectangle([(width - bar_width) // 2, bar_top, (width + bar_width) // 2,
                    bar_top + max(6, height // 120)], fill=brand["accent"])
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out


def _thumb_background(brand: dict, background: Path | None) -> Image.Image:
    if background and background.is_file():
        image = ImageOps.fit(Image.open(background).convert("RGB"), THUMB_SIZE)
        return ImageEnhance.Brightness(image).enhance(THUMB_DARKEN)
    return Image.new("RGB", THUMB_SIZE, brand["dark"])


def make_thumbnail(text: str, channel_name: str, brand: dict, out: Path,
                   background: Path | None = None) -> Path:
    image = _thumb_background(brand, background)
    draw = ImageDraw.Draw(image)
    width, height = THUMB_SIZE
    margin = 70

    draw.rectangle([0, 0, 22, height], fill=brand["accent"])
    label_font = load_font(brand["font_bold"], 30)
    label = channel_name.upper()
    label_width = draw.textlength(label, font=label_font)
    draw.rectangle([margin - 14, 48, margin + label_width + 14, 98], fill=brand["primary"])
    draw.text((margin, 54), label, font=label_font, fill="#FFFFFF")

    font, lines = fit_text(draw, text.upper(), brand["font_bold"], int(width * 0.78),
                           int(height * 0.62), start_size=150)
    line_height = int(font.size * 1.1)
    top = height - margin - line_height * len(lines)
    for i, line in enumerate(lines):
        fill = brand["accent"] if i == len(lines) - 1 else "#FFFFFF"
        draw.text((margin, top + i * line_height), line, font=font, fill=fill,
                  stroke_width=max(2, font.size // 30), stroke_fill="#000000")
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, quality=92)
    return out
