"""Render a deck with LibreOffice and look for visual mess: text on top of other text, or text running into the layout's right-hand stripe."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from mra.ui.api import find_soffice

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PAGE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)


def available() -> bool:
    return bool(find_soffice()) and shutil.which("pdftotext") is not None


def words_by_page(pptx_bytes: bytes) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "d.pptx"
        src.write_bytes(pptx_bytes)
        subprocess.run([find_soffice(), f"-env:UserInstallation={(Path(tmp) / 'p').as_uri()}", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                       check=True, capture_output=True, timeout=240)
        subprocess.run(["pdftotext", "-bbox", str(Path(tmp) / "d.pdf"), str(Path(tmp) / "d.html")], check=True, capture_output=True, timeout=60)
        html = (Path(tmp) / "d.html").read_text(encoding="utf-8")
    pages = []
    for w, h, body in PAGE.findall(html):
        pages.append({"w": float(w), "h": float(h), "words": [(float(a), float(b), float(c), float(d), t) for a, b, c, d, t in WORD.findall(body)]})
    return pages


def problems(pages: list[dict], slides: range, stripe: float = 912.0) -> list[str]:
    out = []
    for n in slides:
        page = pages[n - 1]
        ws = page["words"]
        core = [(x0, y0 + 0.25 * (y1 - y0), x1, y1 - 0.25 * (y1 - y0), t) for x0, y0, x1, y1, t in ws]      # the glyphs, not the line's spare height
        for i, (x0, y0, x1, y1, t) in enumerate(core):
            if x1 > stripe + 0.5 and y1 < page["h"] - 40:
                out.append(f"slide {n}: '{t}' runs into the right-hand stripe (x={x1:.0f})")
            if y1 > page["h"] or x0 < 0:
                out.append(f"slide {n}: '{t}' is off the slide")
            for x2, y2, x3, y3, u in core[i + 1:]:
                iw, ih = min(x1, x3) - max(x0, x2), min(y1, y3) - max(y0, y2)
                if iw > 2 and ih > 1 and iw * ih > 0.25 * min((x1 - x0) * (y1 - y0), (x3 - x2) * (y3 - y2)):
                    out.append(f"slide {n}: '{t}' overlaps '{u}'")
    return out
