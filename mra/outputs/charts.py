"""Charts drawn with Pillow: radar charts and horizontal bar charts like the AWS report's. Deterministic, no system fonts.

A group with a blank rating (value None) is drawn as 'Incomplete', never as 0.
"""
from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

COLOURS = {"green": (0, 204, 0), "amber": (255, 255, 102), "red": (244, 130, 130), "incomplete": (200, 200, 200)}


def _font(size: int):
    return ImageFont.load_default(size=size)


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def radar_png(title: str, labels: list[str], values: list[float | None], guide: float = 80, size: tuple[int, int] = (640, 480)) -> bytes:
    """values are on a 0 to 100 scale; guide is the green ring."""
    w, h = size
    img = Image.new("RGBA", size, "white")
    d = ImageDraw.Draw(img)
    cx, cy, rad, n = w / 2, h / 2 + 10, min(w, h) * 0.30, len(labels)
    ang = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]

    def pt(i: int, v: float) -> tuple[float, float]:
        return cx + rad * v / 100 * math.cos(ang[i]), cy + rad * v / 100 * math.sin(ang[i])

    for ring in (20, 40, 60, 80, 100):
        d.polygon([pt(i, ring) for i in range(n)], outline=(180, 180, 180) if ring != 100 else (0, 0, 0))
    for i in range(n):
        d.line([(cx, cy), pt(i, 100)], fill=(180, 180, 180))
    over = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    poly = [pt(i, v if v is not None else 0) for i, v in enumerate(values)]
    od.polygon(poly, fill=(255, 153, 0, 120), outline=(255, 153, 0, 255))
    img = Image.alpha_composite(img, over)
    d = ImageDraw.Draw(img)
    d.polygon([pt(i, guide) for i in range(n)], outline=(0, 255, 0))
    d.polygon([pt(i, guide) for i in range(n)], outline=(0, 230, 0), width=2)
    f, fb = _font(13), _font(17)
    d.text((w / 2, 14), title, fill="black", font=fb, anchor="mt")
    for ring in (20, 40, 60, 80):
        d.text((cx + 4, cy - rad * ring / 100), str(ring), fill="black", font=_font(11))
    for i, lab in enumerate(labels):
        x, y = pt(i, 118)
        text = lab + ("\n(incomplete)" if values[i] is None else "")
        anchor = "ma" if abs(math.cos(ang[i])) < 0.2 else ("la" if math.cos(ang[i]) > 0 else "ra")
        d.multiline_text((x, y - 8), text, fill="black", font=f, anchor=anchor, align="center")
    return _png(img.convert("RGB"))


def bars_png(title: str, rows: list[tuple[str, float | None, str]], width: int = 700, max_value: float = 5.0) -> bytes:
    """rows: (label, score, colour). First row is normally AVERAGE. Score None draws 'Incomplete'."""
    row_h, top, left = 26, 34, 250
    h = top + row_h * len(rows) + 16
    img = Image.new("RGB", (width, h), "white")
    d = ImageDraw.Draw(img)
    f, fb = _font(12), _font(14)
    d.text((width / 2 + 40, 8), title, fill="black", font=fb, anchor="mt")
    span = width - left - 70
    d.line([(left, top - 4), (left, h - 12)], fill="black", width=1)
    for i, (lab, score, colour) in enumerate(rows):
        y = top + i * row_h
        label = lab if len(lab) <= 38 else lab[:36] + "..."
        d.text((left - 8, y + row_h / 2 - 2), label, fill="black", font=f, anchor="rm")
        if score is None:
            d.text((left + 6, y + row_h / 2 - 2), "Incomplete (a rating is blank)", fill=(110, 110, 110), font=f, anchor="lm")
            continue
        x2 = left + span * score / max_value
        d.rectangle([left, y + 3, x2, y + row_h - 5], fill=COLOURS[colour], outline="black", width=2)
        d.text((x2 + 6, y + row_h / 2 - 2), f"{score:.1f}", fill=(110, 110, 110), font=f, anchor="lm")
    return _png(img)
