"""Text that fits its box: no overflow, no bleeding into the next shape.

The Ingram deck uses Poppins, a wide font that may not be installed where the deck is opened, so nothing here trusts how a viewer happens to render.
Wrapped height is estimated with a deliberately wide character model. A box's budget is the larger of its own inner height and what the template's
own sample text needs (the template's author made that fit); our text then shrinks in half-point steps down to a floor, and only if it still does
not fit is it cut at a word boundary and marked with an ellipsis. Whatever is shortened is reported, never silent.
"""
from __future__ import annotations

import re

from pptx.util import Pt

LINE = 1.45                                    # Poppins line height as a multiple of the font size
_NARROW = set("iljtfIr.,;:'|!()[]/\\ -\u2019")
_WIDE = set("mwMW@%")


def char_em(c: str) -> float:
    if c in _NARROW:
        return 0.34
    if c in _WIDE:
        return 0.88
    if c.isupper():
        return 0.72
    return 0.60 if c.isalpha() else 0.62


def width_pt(text: str, size: float) -> float:
    return sum(char_em(c) for c in text) * size


def wrap(text: str, avail: float, size: float) -> list[str]:
    """Greedy word wrap with the character model; a word longer than a line is split."""
    lines, cur = [], ""
    for word in text.split():
        trial = word if not cur else cur + " " + word
        if width_pt(trial, size) <= avail:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while width_pt(word, size) > avail and len(word) > 1:
            k = max(1, int(len(word) * avail / width_pt(word, size)))
            lines.append(word[:k])
            word = word[k:]
        cur = word
    lines.append(cur)
    return lines


def height_pt(paragraphs: list[str], avail: float, size: float) -> float:
    return sum(max(1, len(wrap(t, avail, size))) * size * LINE for t in paragraphs)


def inner_box(shape, indent_pt: float = 0.0) -> tuple[float, float]:
    """Usable (width, height) in points, after the box's own insets and a safety margin (rounded corners, rendering differences)."""
    body = shape.text_frame._txBody.find("{http://schemas.openxmlformats.org/drawingml/2006/main}bodyPr")

    def ins(name: str, default: int) -> float:
        v = body.get(name) if body is not None else None
        return (int(v) if v is not None else default) / 12700
    w = shape.width / 12700 - ins("lIns", 91440) - ins("rIns", 91440) - indent_pt
    h = shape.height / 12700 - ins("tIns", 45720) - ins("bIns", 45720)
    return max(w, 10) * 0.94, max(h, 8) * 0.96


def first_size(shape, default: float = 18.0) -> float:
    for p in shape.text_frame.paragraphs:
        for r in p.runs:
            if r.font.size:
                return r.font.size.pt
        end = p._p.find("{http://schemas.openxmlformats.org/drawingml/2006/main}endParaRPr")
        if end is not None and end.get("sz"):
            return int(end.get("sz")) / 100
    return default


def fit(shape, paragraphs: list[str], sample: list[str], size: float, *, min_size: float = 9.0, indent_pt: float = 0.0,
        gaps: int = 0, collapse: bool = False) -> dict:
    """Choose a font size (and, as a last resort, shorten) so `paragraphs` fit `shape`. `gaps` is how many empty spacer lines also take room.
    Order: shrink the font; if `collapse` and still too long, run the lines together into flowing text and shrink again; then drop whole items from
    the end; only then cut words from the last one. The result always fits the budget."""
    w, h = inner_box(shape, indent_pt)
    budget = max(h, height_pt(sample, w, size) if sample else 0)
    out, cut = list(paragraphs), False

    def need(items: list[str], sz: float) -> float:
        return height_pt(items, w, sz) + gaps * sz * LINE

    s = size
    while need(out, s) > budget and s > min_size:
        s = max(min_size, s - 0.5)
    if need(out, s) > budget and collapse and len(out) > 1:
        out, s = [" \u2022 ".join(x.strip() for x in out if x.strip())], size
        while need(out, s) > budget and s > min_size:
            s = max(min_size, s - 0.5)
    while need(out, s) > budget and len(out) > 1:
        out.pop()
        cut = True
    guard = 0
    while need(out, s) > budget and guard < 3000:
        words = out[-1].rstrip("\u2026").split()
        out[-1] = (" ".join(words[:-1]) if len(words) > 1 else out[-1].rstrip("\u2026")[:-2].rstrip()) + "\u2026"
        cut, guard = True, guard + 1
    if cut and not out[-1].endswith("\u2026"):
        out[-1] = out[-1].rstrip() + "\u2026"
    return {"size": s, "paragraphs": out, "shortened": cut, "needed": need(out, s), "budget": budget}


def apply_size(shape, size: float, original: float) -> None:
    if size < original - 0.01:
        for p in shape.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(size)


def clean_spaces(s: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", s).strip()
