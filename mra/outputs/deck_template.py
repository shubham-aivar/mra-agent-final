"""Deck templates: the built-in default, checking any deck against it, and making a clean template from a filled-in one.

The default is the AWS MRA readout deck's format, taken from a reference engagement's deck and cleaned of everything
customer-specific. A person can instead supply their own deck; it is filled the same way, and `coverage` says exactly how much of it
the agent can fill (charts, heatmap boxes, sentences, tables, ...) compared with the default.
"""
from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from pptx import Presentation

from ..loader import build_response_set
from ..pipeline import compute_result
from ..report_model import build_report_model
from ..review import workflow as wf

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_NAME = "mra_readout_default.pptx"
DEFAULT_LABEL = "AWS MRA readout format (built in)"
ANSWER_SLOTS = (1, 2, 17, 18, 19, 20, 21, 22)         # the answers the deck shows on its text slides
LABELS = {"cover_slides": "Cover slides", "charts": "Charts (scores and radars)", "heatmap_boxes": "Heatmap boxes",
          "focus_boxes": "Areas-of-focus sentences", "exec_boxes": "Executive-summary boxes", "activity_boxes": "Activity-output boxes",
          "key_observation_lists": "Key-observation lists", "observation_rows": "Observation and action rows",
          "qa_rows": "Question-and-answer rows"}


@lru_cache(maxsize=1)
def default_template_bytes() -> bytes:
    f = TEMPLATE_DIR / DEFAULT_NAME
    if not f.is_file():
        raise FileNotFoundError(f"the built-in deck template is missing ({f}); reinstall, or pass a deck with --pptx-template")
    return f.read_bytes()


# ---- models used to count and to clean -----------------------------------------------------------------------------------
def _complete_model(cfg: dict, score: int = 3) -> dict:
    ratings = [{"question_id": q.id, "score": score} for q in cfg["questions"] if q.type == "rating" and q.perspective]
    rs = build_response_set({"set_id": "probe", "customer_label": "Probe", "ratings": ratings, "answers": [], "evidence": []}, cfg)
    return build_report_model(compute_result(rs, cfg), wf.new_state("probe"), "", [], cfg)


def blank_model(cfg: dict) -> dict:
    """A model that writes placeholders instead of data: how the default template is made from a filled deck."""
    m = _complete_model(cfg)
    m["customer"] = "[Customer Name]"
    m["details"] = {"facilitator": "[Company Name]", "workshop_date": "[Date]", "readout_date": "[Date]", "presenter": "[Presenter]"}
    for rows in (m["perspectives"], m["workstreams"], m["activities"]):
        for g in rows:
            g.update(status="incomplete", score=None, radar=None, colour="incomplete")
    for a in m["activities"]:
        a.update(needs_action=False, actions=[], observation={"text": "", "source": "tool"})
    for q in m["questions"]:
        q.update(score=None, level_text=None)
    for t in m["text_answers"]:
        t["answer"] = f"[Answer to Q{t['number']}]" if t["number"] in ANSWER_SLOTS else ""
    sentence = "[Generated from the scores]"
    m["focus"] = {"perspective_sentences": [sentence] * 3, "workstream_sentences": [sentence] * 3, "strengths": ["[Strength]"] * 2,
                  "gaps": ["[Gap]"] * 4, "key_observations": [{"strong": ["[Strength]"] * 2, "weak": ["[Gap]"] * 3}] * 2, "incomplete": []}
    m["_template_mode"] = True
    return m


# ---- how much of a deck can be filled ----------------------------------------------------------------------------------
def _stats(cfg: dict, data: bytes) -> dict:
    from .pptx_out import render_pptx
    m = _complete_model(cfg)
    render_pptx(m, data)
    return m["deck_stats"]


_COVERAGE: dict[tuple[str, str], dict] = {}
_EXPECTED: dict[str, dict] = {}


def _expected(cfg: dict) -> dict:
    """What the built-in default offers, filled once per configuration (its structure only changes if the default or the configuration does)."""
    key = cfg["config_hash"] + template_hash(None)
    if key not in _EXPECTED:
        _EXPECTED[key] = _stats(cfg, default_template_bytes())
    return _EXPECTED[key]


def coverage(data: bytes, cfg: dict) -> dict:
    """What the agent can fill in this deck, compared with the default. Raises ValueError if the file cannot be read as a deck."""
    import hashlib
    key = (hashlib.sha256(data).hexdigest(), cfg["config_hash"])
    if key not in _COVERAGE:
        _COVERAGE[key] = _coverage(data, cfg)
    return _COVERAGE[key]


def _coverage(data: bytes, cfg: dict) -> dict:
    from .ingram_deck import ingram_coverage, ingram_template_bytes, is_ingram
    if is_ingram(data):
        try:
            return ingram_coverage(data, ingram_template_bytes(), lambda: _complete_model(cfg))
        except Exception as e:
            raise ValueError(f"this file cannot be filled as a deck ({type(e).__name__}: {str(e)[:120]})") from e
    try:
        found = _stats(cfg, data)
    except Exception as e:
        raise ValueError(f"this file cannot be filled as a deck ({type(e).__name__}: {str(e)[:120]})") from e
    expected = _expected(cfg)
    items, warnings = [], []
    for key, label in LABELS.items():
        f, e = int(found.get(key, 0)), int(expected.get(key, 0))
        items.append({"key": key, "label": label, "found": f, "expected": e, "ok": f >= e})
        if f < e:
            warnings.append(f"{label}: found {f} of {e}; the rest will not be filled")
    total_f, total_e = sum(i["found"] for i in items), sum(i["expected"] for i in items)
    if all(i["found"] == 0 for i in items):
        warnings = ["this does not look like the AWS MRA readout deck: nothing in it can be filled"]
    return {"items": items, "found": total_f, "expected": total_e, "complete": all(i["ok"] for i in items), "warnings": warnings,
            "format": "aws"}


def template_hash(data: bytes | None) -> str:
    import hashlib
    if data is None:
        from .ingram_deck import ingram_template_bytes
        data = ingram_template_bytes()
    return hashlib.sha256(data).hexdigest()


def describe(cov: dict) -> str:
    if cov["complete"]:
        return f"same format as the {'Ingram MAP deliverable' if cov.get('format') == 'ingram' else 'AWS MRA readout'}: every part can be filled"
    return "partly matches the default: " + "; ".join(cov["warnings"])


# ---- text of a deck and of its whole package (for the leak check) ----------------------------------------------------------
def _walk(shapes):
    for sh in shapes:
        yield sh
        if sh.shape_type == 6:
            yield from _walk(sh.shapes)


def deck_lines(data: bytes) -> set[str]:
    """Every line of text a person can see in the deck: shapes, tables and notes."""
    prs = Presentation(io.BytesIO(data))
    out: set[str] = set()
    for slide in prs.slides:
        texts = []
        for sh in _walk(slide.shapes):
            if sh.has_text_frame:
                texts.append(sh.text_frame.text)
            if getattr(sh, "has_table", False) and sh.has_table:
                texts += [c.text for r in sh.table.rows for c in r.cells]
        if slide.has_notes_slide:
            texts.append(slide.notes_slide.notes_text_frame.text if slide.notes_slide.notes_text_frame is not None else "")
        for t in texts:
            out.update(x.strip() for x in re.split(r"[\n\v]", t) if x.strip())
    return out


_PARA = re.compile(r"<a:p[ >].*?</a:p>", re.S)
_RUN = re.compile(r"<a:t[^>]*>([^<]*)</a:t>")
_CHART_V = re.compile(r"<c:v>([^<]*)</c:v>")
_XLSX_T = re.compile(r"<t[^>]*>([^<]*)</t>|<v>([^<]*)</v>")


def package_text(data: bytes) -> str:
    """All text in every part of the package, lower-cased: slide text by paragraph, chart values, embedded workbooks, and the raw
    document properties and comment authors."""
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            raw = z.read(name)
            if name.endswith(".xlsx"):
                with zipfile.ZipFile(io.BytesIO(raw)) as x:
                    for n in x.namelist():
                        if n.endswith(".xml"):
                            parts += [a or b for a, b in _XLSX_T.findall(x.read(n).decode("utf-8", "replace"))]
                continue
            if not name.endswith((".xml", ".rels")):
                continue
            xml = raw.decode("utf-8", "replace")
            if name.startswith(("ppt/slides/", "ppt/notesSlides/", "ppt/slideLayouts/", "ppt/slideMasters/")):
                parts += ["".join(_RUN.findall(p)) for p in _PARA.findall(xml)]
            elif name.startswith("ppt/charts/"):
                parts += _CHART_V.findall(xml)
            else:
                parts.append(xml)
    return "\n".join(parts).lower()


# ---- scrubbing and making the default ------------------------------------------------------------------------------------------
def _rewrite(data: bytes, edits: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, edits.get(item.filename, zin.read(item.filename)))
    return buf.getvalue()


def personal_names(data: bytes) -> list[str]:
    """People's names recorded in the package: the last editor and the comment authors."""
    names: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if "docProps/core.xml" in z.namelist():
            core = z.read("docProps/core.xml").decode("utf-8", "replace")
            names += re.findall(r"<cp:lastModifiedBy>([^<]+)</cp:lastModifiedBy>", core)
            names += [n for n in re.findall(r"<dc:creator>([^<]+)</dc:creator>", core) if n != "Microsoft Office User"]
        if "ppt/commentAuthors.xml" in z.namelist():
            names += re.findall(r'name="([^"]+)"', z.read("ppt/commentAuthors.xml").decode("utf-8", "replace"))
    return sorted({n.strip() for n in names if n.strip()})


def scrub_properties(data: bytes) -> bytes:
    edits: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        if "docProps/core.xml" in names:
            core = z.read("docProps/core.xml").decode("utf-8")
            core = re.sub(r"<cp:lastModifiedBy>[^<]*</cp:lastModifiedBy>", "<cp:lastModifiedBy></cp:lastModifiedBy>", core)
            core = re.sub(r"<dc:creator>[^<]*</dc:creator>", "<dc:creator></dc:creator>", core)
            core = re.sub(r"<dc:title>[^<]*</dc:title>", "<dc:title>AWS MRA readout (template)</dc:title>", core)
            edits["docProps/core.xml"] = core.encode("utf-8")
        if "ppt/commentAuthors.xml" in names:
            edits["ppt/commentAuthors.xml"] = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                                               b'<p:cmAuthorLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                                               b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                                               b'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
    return _rewrite(data, edits)


def residue(out: bytes, fragments: Iterable[str]) -> list[str]:
    pkg = package_text(out)
    return sorted({f for f in fragments if f.lower() in pkg})


def make_default_template(src: bytes, cfg: dict, forbid: Iterable[str] = ()) -> tuple[bytes, dict]:
    """Turn a filled-in deck into a clean template. Everything the agent fills is replaced by a placeholder; personal names in the
    properties are removed; then every part of the package is scanned for any text that was removed, for the extra strings in
    `forbid` (for example the customer's name and answers), and for the people's names. Any hit stops the build."""
    from .pptx_out import render_pptx
    model = blank_model(cfg)
    out = render_pptx(model, src)
    stats = model["deck_stats"]
    names = personal_names(src)
    out = scrub_properties(out)
    removed = {t for t in deck_lines(src) - deck_lines(out) if len(t) >= 12}
    extra = {t.strip() for t in forbid if len(t.strip()) >= 4}
    # AWS's own question and rating wording is not a leak even when a customer's answer reused the same words ("primary drivers", ...)
    aws_text = " ".join([q.text for q in cfg["questions"]] + [lv for q in cfg["questions"] for lv in q.levels]).lower()
    candidates = removed | extra | set(names)
    hits = [h for h in residue(out, candidates) if h.lower() not in aws_text]
    if hits:
        raise ValueError(f"the template would still contain {len(hits)} removed or forbidden text(s), for example: {hits[:3]}")
    return out, {"stats": stats, "checked_removed_lines": len(removed), "checked_forbidden": len(extra), "personal_names_removed": len(names),
                 "aws_wording_ignored": len([h for h in residue(out, candidates) if h.lower() in aws_text])}
