"""Fill the AWS MRA readout deck from the report model.

The deck is the AWS template (a clean one, or a previously filled one). Everything that depends on the customer's
answers is overwritten: charts (and their embedded workbooks), the heatmap colours, the areas-of-focus sentences,
the executive summary, the activity outputs, the key observations, the observation tables and the question-and-answer
appendix. Shapes are found by slide title, chart series name, activity name and question number, never by slide
number. Facilitator instruction boxes are removed. A group with a blank rating is shown as Incomplete (grey, no bar).

Things the questionnaire cannot supply (cost slides, which delivery-model variant to keep, the AMS fit score, next-step
owners, the logo) are left alone and listed by the 'todo' output. After filling, the deck is scanned for the old
customer's name; any leftover is reported in model['deck_warnings'].
"""
from __future__ import annotations

import copy
import re
from datetime import date
from io import BytesIO

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.util import Emu

HEX = {"green": "00CC00", "amber": "FFFF66", "red": "FF9999", "incomplete": "BFBFBF"}
_ACT_TOKEN = re.compile(r"\.\.\.$")


# ---- generic helpers --------------------------------------------------------
def _walk(shapes):
    for sh in shapes:
        yield sh
        if sh.shape_type == 6:
            yield from _walk(sh.shapes)


def _title(slide) -> str:
    """The slide's title: its title placeholder, else the text box named Title/Object 6/TextBox nearest the top."""
    for sh in slide.shapes:
        if sh.is_placeholder and sh.placeholder_format.type is not None and "TITLE" in str(sh.placeholder_format.type) and sh.has_text_frame:
            return sh.text_frame.text.strip()
    cands = [sh for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip() and sh.top is not None and sh.top < Emu(1_000_000)
             and sh.width and sh.width > Emu(8_000_000)]
    cands.sort(key=lambda s: (s.top, s.left))
    return cands[0].text_frame.text.strip() if cands else ""


def _set_text(shape, lines: list[str] | str) -> None:
    """Replace a shape's text, reusing the formatting of its first non-empty paragraph for every new paragraph."""
    if isinstance(lines, str):
        lines = lines.split("\n")
    tf = shape.text_frame
    paras = tf.paragraphs
    proto = next((p for p in paras if p.text.strip()), paras[0])
    proto_xml = copy.deepcopy(proto._p)
    for p in list(paras):
        p._p.getparent().remove(p._p)
    body = tf._txBody
    for line in lines:
        p_xml = copy.deepcopy(proto_xml)
        for child in list(p_xml):
            if child.tag.endswith("}r") or child.tag.endswith("}br") or child.tag.endswith("}fld"):
                p_xml.remove(child)
        body.append(p_xml)
        from pptx.text.text import _Paragraph
        para = _Paragraph(p_xml, tf)
        run = para.add_run()
        run.text = line
        src = next((r for r in proto.runs), None)
        if src is not None and src._r.find("{http://schemas.openxmlformats.org/drawingml/2006/main}rPr") is not None:
            old = run._r.find("{http://schemas.openxmlformats.org/drawingml/2006/main}rPr")
            if old is not None:
                run._r.remove(old)
            run._r.insert(0, copy.deepcopy(src._r.find("{http://schemas.openxmlformats.org/drawingml/2006/main}rPr")))


def _remove(shape) -> None:
    shape._element.getparent().remove(shape._element)


def _fill(shape, colour_key: str) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(HEX[colour_key])


def _lookup(label: str, names: list[str]) -> str | None:
    base = _ACT_TOKEN.sub("", label).rstrip(".")
    for n in names:
        if n == label or n.startswith(base):
            return n
    return None


def _nice_date(iso: str | None) -> str:
    try:
        return date.fromisoformat(iso).strftime("%a %b %d %Y") if iso else ""
    except ValueError:
        return iso or ""


# ---- charts -----------------------------------------------------------------
def _update_charts(prs, m: dict) -> int:
    persp = {p["name"]: p for p in m["perspectives"]}
    ws = {w["name"]: w for w in m["workstreams"]}
    acts = {w["name"]: {a["name"]: a for a in m["activities"] if a["workstream"] == w["id"]} for w in m["workstreams"]}
    n = 0
    for slide in prs.slides:
        for sh in slide.shapes:
            if not (getattr(sh, "has_chart", False) and sh.has_chart):
                continue
            ch = sh.chart
            pl = ch.plots[0]
            cats, name = list(pl.categories), pl.series[0].name
            new = None
            if "RADAR" in str(ch.chart_type):
                if name == "Perspectives":
                    new = [persp[c]["radar"] if c in persp else None for c in cats]
                elif name == "Workstreams":
                    new = [ws[c]["radar"] if c in ws else None for c in cats]
                colours = None
            elif name == "Average Readiness Scores":
                keys = [_lookup(c, list(ws)) for c in cats]
                new, colours = [ws[k]["score"] if k else None for k in keys], [ws[k]["colour"] if k else "incomplete" for k in keys]
            elif name in acts:
                rows, colours = [], []
                for c in cats:
                    g = ws[name] if c == "AVERAGE" else acts[name].get(_lookup(c, list(acts[name])) or "")
                    rows.append(g["score"] if g else None)
                    colours.append(g["colour"] if g else "incomplete")
                new = rows
            if new is None:
                continue
            cd = CategoryChartData()
            cd.categories = cats
            for ser in pl.series:
                cd.add_series(ser.name, new if ser.name == name else list(ser.values))
            ch.replace_data(cd)
            if colours is not None:
                for i, c in enumerate(colours):
                    f = pl.series[0].points[i].format.fill
                    f.solid()
                    f.fore_color.rgb = RGBColor.from_string(HEX[c])
            n += 1
    return n


def _recolour_heatmap(prs, m: dict) -> int:
    by_name = {a["name"]: a for a in m["activities"]}
    n = 0
    for slide in prs.slides:
        if "MRA Workstream Summary" not in _title(slide):
            continue
        for sh in _walk(slide.shapes):
            if sh.has_text_frame and sh.text_frame.text.strip() in by_name and sh.fill.type == 1:
                _fill(sh, by_name[sh.text_frame.text.strip()]["colour"])
                n += 1
    return n


# ---- text slides ---------------------------------------------------------------
def _focus_slides(prs, m: dict) -> int:
    f = m["focus"]
    n = 0
    for slide in prs.slides:
        if _title(slide) != "MRA Areas of Focus":
            continue
        series = next((sh.chart.plots[0].series[0].name for sh in slide.shapes if getattr(sh, "has_chart", False) and sh.has_chart), None)
        sentences = f["perspective_sentences"] if series == "Perspectives" else f["workstream_sentences"] if series == "Workstreams" else None
        if sentences is None:
            continue
        boxes = sorted((sh for sh in slide.shapes if sh.has_text_frame and sh.left > Emu(7_000_000) and sh.width < Emu(6_500_000)
                        and sh.height > Emu(1_000_000)), key=lambda s: s.top)
        for i, box in enumerate(boxes[:3]):
            _set_text(box, sentences[i] if i < len(sentences) else "")
            n += 1
    return n


def _answer(m: dict, number: int) -> str:
    return next((t["answer"] for t in m["text_answers"] if t["number"] == number), "")


def _exec_slides(prs, m: dict) -> dict:
    f = m["focus"]
    c = {"exec_boxes": 0, "activity_boxes": 0, "key_observation_lists": 0}
    for slide in prs.slides:
        t = _title(slide)
        if t == "MRA Executive Summary":
            big = sorted((sh for sh in slide.shapes if sh.has_text_frame and sh.width > Emu(4_500_000) and sh.top < Emu(2_000_000)
                          and sh.height > Emu(2_000_000)), key=lambda s: s.left)
            if len(big) >= 2:
                _set_text(big[0], _answer(m, 2) or "")
                _set_text(big[1], _answer(m, 1) or "")
                c["exec_boxes"] += 2
            small = [sh for sh in slide.shapes if sh.has_text_frame and Emu(3_000_000) < sh.width < Emu(4_500_000) and sh.top > Emu(3_800_000)
                     and sh.height < Emu(500_000)]
            left = sorted((s for s in small if s.left < Emu(7_000_000)), key=lambda s: s.top)
            right = sorted((s for s in small if s.left >= Emu(7_000_000)), key=lambda s: s.top)
            for i, box in enumerate(left):
                _set_text(box, f["strengths"][i] if i < len(f["strengths"]) else "")
                c["exec_boxes"] += 1
            for i, box in enumerate(right):
                _set_text(box, f["gaps"][i] if i < len(f["gaps"]) else "")
                c["exec_boxes"] += 1
        elif t == "MRA Activity Outputs":
            boxes = sorted((sh for sh in slide.shapes if sh.has_text_frame and sh.width > Emu(5_000_000) and sh.height > Emu(2_000_000)
                            and "FACILITATOR" not in sh.text_frame.text), key=lambda s: (round(s.top / 400_000), s.left))
            heads = " ".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.startswith("Activity "))
            numbers = [17, 18, 19, 20] if "Activity 1" in heads else [21, 22] if "Activity 3" in heads else []
            for box, num in zip(boxes, numbers):
                _set_text(box, _answer(m, num))
                c["activity_boxes"] += 1
        elif t in ("MRA Readiness Scores I", "MRA Readiness Scores II"):
            idx = 0 if t.endswith("Scores I") else 1
            pages = f["key_observations"]
            if idx >= len(pages):
                continue
            box = next((sh for sh in slide.shapes if sh.has_text_frame and sh.left > Emu(7_000_000) and sh.height > Emu(3_500_000)
                        and "FACILITATOR" not in sh.text_frame.text), None)
            if box is not None:
                strong, weak = pages[idx]["strong"], pages[idx]["weak"]
                lines = [*strong, "", *weak] if strong and weak else [*strong, *weak]    # an empty paragraph keeps the gap
                _set_text(box, lines)
                c["key_observation_lists"] += 1
    return c


def _fill_cell(cell, text: str) -> None:
    tf = cell.text_frame
    paras = tf.paragraphs
    first = paras[0]
    for p in paras[1:]:
        p._p.getparent().remove(p._p)
    if first.runs:
        first.runs[0].text = text
        for r in first.runs[1:]:
            r._r.getparent().remove(r._r)
    else:
        first.add_run().text = text


def _observation_tables(prs, m: dict) -> int:
    by_ws = {w["name"]: w["id"] for w in m["workstreams"]}
    by_name = {(a["workstream"], a["name"]): a for a in m["activities"]}
    n = 0
    for slide in prs.slides:
        title = _title(slide)
        wsid = by_ws.get(title) or by_ws.get({"Migration Project Plan": "Customer Migration Project Plan"}.get(title, ""))
        if wsid is None:
            continue
        for sh in slide.shapes:
            if not (getattr(sh, "has_table", False) and sh.has_table):
                continue
            tb = sh.table
            if [c.text.strip() for c in tb.rows[0].cells] != ["Category", "Summary Observations", "Actions & Next Steps"]:
                continue
            for row in list(tb.rows)[1:]:
                a = by_name.get((wsid, row.cells[0].text.strip()))
                if a is None:
                    continue
                _fill_cell(row.cells[1], a["observation"]["text"])
                acts = "; ".join(x["text"] for x in a["actions"]) if a["actions"] else (
                    "" if m.get("_template_mode") else "No action required." if not a["needs_action"] else "")
                _fill_cell(row.cells[2], acts)
                n += 1
    return n


def _qa_tables(prs, m: dict) -> int:
    qres = {f"Q{q['number']}": q for q in m["questions"]}
    ans = {f"Q{t['number']}": t for t in m["text_answers"]}
    n = 0
    for slide in prs.slides:
        for sh in slide.shapes:
            if not (getattr(sh, "has_table", False) and sh.has_table):
                continue
            tb = sh.table
            if [c.text.strip() for c in tb.rows[0].cells] != ["#", "Question", "Response", "Comments"]:
                continue
            for row in list(tb.rows)[1:]:
                key = row.cells[0].text.strip()
                if key in qres:
                    q = qres[key]
                    _fill_cell(row.cells[2], "" if q["score"] is None or m.get("_template_mode") else str(q["score"]))
                    _fill_cell(row.cells[3], "" if m.get("_template_mode") else (q["level_text"] or "(no rating yet)"))
                elif key in ans:
                    a = ans[key]
                    blank = bool(m.get("_template_mode"))
                    _fill_cell(row.cells[2], a["answer"] if a["type"] == "yes_no" and not blank else "")
                    _fill_cell(row.cells[3], a["answer"] if a["type"] == "text" and not blank else "")
                else:
                    continue
                n += 1
    return n


def _row_slides(prs, m: dict) -> int:
    """'MRA Observations & Actions - <workstream>' slides, built from rectangles in rows instead of a table."""
    by_ws = {w["name"]: w["id"] for w in m["workstreams"]}
    by_name = {(a["workstream"], a["name"]): a for a in m["activities"]}
    n = 0
    for slide in prs.slides:
        t = _title(slide)
        if not t.startswith("MRA Observations & Actions \u2013 ") or t.endswith("All Workstreams"):
            continue
        wsid = by_ws.get(t.split("\u2013 ", 1)[1].strip())
        if wsid is None:
            continue
        shapes = [sh for sh in slide.shapes if sh.has_text_frame]
        for name_box in (s for s in shapes if (wsid, s.text_frame.text.strip()) in by_name and s.left < Emu(1_000_000)):
            a = by_name[(wsid, name_box.text_frame.text.strip())]
            row = [s for s in shapes if abs(s.top - name_box.top) < Emu(40_000) and s is not name_box]
            obs = next((s for s in row if Emu(4_000_000) < s.left < Emu(6_000_000)), None)
            act = next((s for s in row if s.left > Emu(8_000_000)), None)
            if obs is not None:
                _set_text(obs, a["observation"]["text"])
            if act is not None:
                none_text = "" if m.get("_template_mode") else "No action required."
                _set_text(act, "; ".join(x["text"] for x in a["actions"]) if a["actions"] else none_text)
            n += 1
    return n


def _covers(prs, m: dict) -> int:
    d = m["details"]
    old_customer = None
    n = 0
    for slide in prs.slides:
        if _title(slide) != "Migration Readiness Assessment (MRA)":
            continue
        sub = next((sh.text_frame.text.strip() for sh in slide.shapes if sh.is_placeholder and "SUBTITLE" in str(sh.placeholder_format.type)), "")
        when = d.get("workshop_date") if "Workshop" in sub else d.get("readout_date")
        boxes = sorted((sh for sh in slide.shapes if sh.has_text_frame and not sh.is_placeholder), key=lambda s: s.top)
        n += 1
        for b in boxes:
            txt = b.text_frame.text.strip()
            if txt == "Facilitated By":
                continue
            if txt.startswith("[IMAGE"):
                if not m.get("_template_mode"):
                    _set_text(b, "")
            elif txt == "[Company Name]" or (b.top > Emu(5_000_000) and b is boxes[-3 if len(boxes) >= 3 else 0]):
                _set_text(b, d.get("facilitator") or "TBD")
            elif b.top > Emu(5_000_000) and b is boxes[-2]:
                _set_text(b, _nice_date(when) or "TBD")
            elif b.top > Emu(5_000_000) and b is boxes[-1]:
                _set_text(b, d.get("presenter") or "")
            elif Emu(1_500_000) < b.top < Emu(3_000_000):
                old_customer = old_customer or txt
                _set_text(b, m["customer"])
    m.setdefault("_old_customer", old_customer)
    return n


def _clean(prs, m: dict) -> tuple[int, int]:
    removed = 0
    for slide in prs.slides:
        for sh in list(_walk(slide.shapes)):
            if sh.has_text_frame and sh.text_frame.text.strip().startswith("FACILITATOR"):
                _remove(sh)
                removed += 1
    dropped = 0
    ids = prs.slides._sldIdLst
    for sldid, slide in list(zip(list(ids), list(prs.slides))):
        if _title(slide).lower().startswith("guidance for using this deck") or any(
                "Delete before using" in sh.text_frame.text for sh in slide.shapes if sh.has_text_frame):
            prs.part.drop_rel(sldid.rId)
            ids.remove(sldid)
            dropped += 1
    for slide in prs.slides:                                   # '<customer>' in the cost slides
        for sh in _walk(slide.shapes):
            if sh.has_text_frame and "<customer>" in sh.text_frame.text:
                for p in sh.text_frame.paragraphs:
                    for r in p.runs:
                        r.text = r.text.replace("<customer>", m["customer"])
    return removed, dropped


def _leak_scan(prs, old: str | None, customer: str) -> list[str]:
    if not old or old.strip().lower() == customer.strip().lower():
        return []
    hits = []
    for i, slide in enumerate(prs.slides, 1):
        for sh in _walk(slide.shapes):
            texts = []
            if sh.has_text_frame:
                texts.append(sh.text_frame.text)
            if getattr(sh, "has_table", False) and sh.has_table:
                texts += [c.text for r in sh.table.rows for c in r.cells]
            if any(old.lower() in t.lower() for t in texts):
                hits.append(f"slide {i} still mentions '{old}' (the template's customer)")
                break
    return hits


def render_pptx(m: dict, template: bytes | None = None) -> bytes:
    """Fill the AWS readout deck. With no template the built-in default is used (the reference deck's format, cleaned of customer content).
    Template mode (m['_template_mode']) writes placeholders instead of data and keeps the AWS instruction boxes: it is how the default is made."""
    if template is None:
        from .deck_template import default_template_bytes
        template = default_template_bytes()
    prs = Presentation(BytesIO(template))
    stats = {"cover_slides": _covers(prs, m)}
    stats["charts"] = _update_charts(prs, m)
    stats["heatmap_boxes"] = _recolour_heatmap(prs, m)
    stats["focus_boxes"] = _focus_slides(prs, m)
    stats.update(_exec_slides(prs, m))
    stats["observation_rows"] = _observation_tables(prs, m) + _row_slides(prs, m)
    stats["qa_rows"] = _qa_tables(prs, m)
    if m.get("_template_mode"):
        stats.update({"facilitator_boxes_removed": 0, "slides_dropped": 0})
        m["deck_warnings"] = []
    else:
        stats["facilitator_boxes_removed"], stats["slides_dropped"] = _clean(prs, m)
        m["deck_warnings"] = _leak_scan(prs, m.get("_old_customer"), m["customer"])
    m["deck_stats"] = stats
    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()
