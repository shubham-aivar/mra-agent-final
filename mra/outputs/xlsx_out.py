"""Excel workbook in the AWS 'Questions, Actions and Scores' layout, followed by our own sheets.

Sheets 1 to 3 have the same names, columns and order as the AWS export, so they can be compared with it or pasted
over it. After them: Summary, Traceability, To validate and (if used) Adjustments. Scores are values written by code.
A group with a blank rating has an empty score cell (never 0). Every text cell passes through safe_cell, so a
customer's text that starts with = + - or @ can never run as a formula.
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..traceability import safe_cell

FONT = "Arial"
FILL = {"red": "F4B6B6", "amber": "FFE699", "green": "C6E0B4", "incomplete": "D9D9D9"}
HEAD = PatternFill("solid", fgColor="1F3864")
thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
TYPE_NAME = {"rating": "Rating", "yes_no": "Yes/No", "text": "Text"}
Q_HEADER = ["Section", "Category", "#", "Question", "Response Type", "Response", "Rating Guide (if applicable)", "Comments",
            "Rating 1", "Rating 2", "Rating 3", "Rating 4", "Rating 5"]
A_HEADER = ["Section", "Readiness Activity", "Workstream Phase", "Summary Observations", "Action", "Priority", "Effort",
            "Target Completion Date", "Benefit", "Owner", "Status"]


def _f(bold=False, color="000000", size=10, italic=False):
    return Font(name=FONT, bold=bold, color=color, size=size, italic=italic)


def _table(ws, header: list[str], rows: list[list], widths: list[int], wrap: set[int] | None = None) -> None:
    for c, h in enumerate(header, 1):
        cell = ws.cell(1, c, h)
        cell.font, cell.fill, cell.border = _f(True, "FFFFFF"), HEAD, BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for r, row in enumerate(rows, 2):
        for c, v in enumerate(row, 1):
            cell = ws.cell(r, c, safe_cell(v))
            cell.font, cell.border = _f(), BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=bool(wrap and c in wrap))
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{len(rows) + 1}"


def _questions_rows(m: dict) -> list[list]:
    rated = {q["question_id"]: q for q in m["questions"]}
    answers = {a["question_id"]: a["answer"] for a in m["text_answers"]}
    rows = []
    for q in sorted(m["question_bank"].values(), key=lambda x: x.number):
        lv = list(q.levels) + [None] * (5 - len(q.levels))
        if q.type == "rating":
            r = rated[q.id]
            rows.append([q.section, q.category, q.number, q.text, "Rating", r["score"], r["level_text"], r["comment"] or None, *lv])
        elif q.type == "yes_no":
            rows.append([q.section, q.category, q.number, q.text, "Yes/No", answers.get(q.id) or None, None, None, *lv])
        else:
            rows.append([q.section, q.category, q.number, q.text, "Text", None, None, answers.get(q.id) or None, *lv])
    return rows


def _scores_rows(m: dict) -> list[list]:
    persp = {p["name"]: p for p in m["perspectives"]}
    cat = {c["id"]: c for c in m["categories"]}
    rows, seen = [], set()
    for a in m["areas"]:
        sec = a["section"]
        if sec not in seen:
            seen.add(sec)
            rows.append([sec, None, persp[sec]["score"] if sec in persp else None])
        score = cat[a["id"]]["score"] if a["id"] in cat else None
        if a.get("module") and a["module"] in {x["id"] for x in m["modules"]}:
            score = next(x["score"] for x in m["modules"] if x["id"] == a["module"])
        rows.append([sec, a["name"], score])
    return rows


def render_xlsx(m: dict, template: bytes | None = None) -> bytes:
    wb = Workbook()
    q = wb.active
    q.title = "Questions"
    _table(q, Q_HEADER, _questions_rows(m), [14, 26, 6, 70, 13, 10, 45, 60, 30, 30, 30, 30, 30], wrap={4, 7, 8, 9, 10, 11, 12, 13})
    act = wb.create_sheet("Actions")
    a_rows = [[r["activity"]["workstream_name"], r["activity"]["name"], r["activity"]["phase"], r["activity"]["observation"]["text"],
               r["action"]["text"], r["action"]["priority"], r["action"]["effort"], r["action"]["due_date"], r["action"]["benefit"],
               r["action"]["owner"], r["action"]["status"]] for r in m["action_rows"]]
    _table(act, A_HEADER, a_rows, [28, 32, 14, 60, 70, 10, 10, 16, 20, 22, 16], wrap={4, 5})
    sc = wb.create_sheet("Scores")
    srows = _scores_rows(m)
    _table(sc, ["Section", "Category", "Score"], srows, [28, 46, 10])
    for r in range(2, len(srows) + 2):
        sc.cell(r, 3).number_format = "0.0"

    s = wb.create_sheet("Summary")
    s.column_dimensions["A"].width = 36
    s.column_dimensions["B"].width = 78
    s["A1"] = m["title"]
    s["A1"].font = _f(True, "1F3864", 14)
    r = 3

    def put(key: str, value, bold: bool = False, formula: bool = False) -> None:
        nonlocal r
        s.cell(r, 1, key).font = _f(True)
        c = s.cell(r, 2, value if formula else safe_cell(value))      # only our own formulas bypass the guard
        c.font = _f(bold)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        r += 1

    d = m["details"]
    for k, v in [("Status", m["status"]), ("Status note", m["status_note"]), ("Generated", m["generated_at"]),
                 ("Facilitator", d.get("facilitator") or "TBD"), ("Workshop date", d.get("workshop_date") or "TBD"),
                 ("Readout date", d.get("readout_date") or "TBD"), ("Scope note", m["scope_note"]), ("Text engine", m["engine"]),
                 ("Config hash", m["config_hash"]), ("Config versions", ", ".join(f"{a}={b}" for a, b in m["versions"].items()))]:
        put(k, v, bold=(k == "Status"))
    for note in m["placeholder_notes"]:
        put("Placeholder / proposed / inferred", note)
    r += 1
    s.cell(r, 1, "Perspective scores (1 to 5)").font = _f(True, "1F3864", 12)
    r += 1
    for p in m["perspectives"]:
        put(p["name"], "Incomplete (a rating is blank)" if p["status"] != "complete" else p["score"])
        s.cell(r - 1, 2).fill = PatternFill("solid", fgColor=FILL[p["colour"]])
        s.cell(r - 1, 2).alignment = Alignment(horizontal="left")
    r += 1
    s.cell(r, 1, "Workstream scores (1 to 5)").font = _f(True, "1F3864", 12)
    r += 1
    for w in m["workstreams"]:
        put(w["name"], "Incomplete (a rating is blank)" if w["status"] != "complete" else w["score"])
        s.cell(r - 1, 2).fill = PatternFill("solid", fgColor=FILL[w["colour"]])
        s.cell(r - 1, 2).alignment = Alignment(horizontal="left")
    r += 1

    tr = wb.create_sheet("Traceability")
    cols = ["question_id", "number", "section", "category", "activity", "status", "score", "original_score", "level_text",
            "evidence_status", "traceable", "evidence_ids", "link_quotes", "guide_level", "adjusted"]
    mx = [q_ for q_ in m["questions"]]

    def flat(v):
        return ";".join(map(str, v)) if isinstance(v, list) else v

    _table(tr, [c.replace("_", " ").capitalize() for c in cols], [[flat(q_[c]) for c in cols] for q_ in mx],
           [11, 8, 13, 34, 9, 11, 7, 9, 60, 14, 10, 22, 60, 10, 10], wrap={9, 13})
    n_q = len(mx)
    val = wb.create_sheet("To validate")

    def draft(v: dict) -> str:
        f = v["followup"]
        return "" if not f else f"To: {', '.join(f['audience_roles'])}\n" + "\n".join(f"- {x}" for x in f["questions"])

    _table(val, ["Item", "Kind", "Ref", "Reason", "Resolution", "Note", "Follow-up draft (accepted)"],
           [[v["item_id"], v["kind"], v["ref"], v["reason"], v["resolution"], v["note"], draft(v)] for v in m["validation"]],
           [8, 22, 8, 80, 14, 40, 70], wrap={4, 6, 7})
    n_v = len(m["validation"])
    if m["adjustments"]:
        adj = wb.create_sheet("Adjustments")
        _table(adj, ["Question", "Original rating", "Adjusted rating", "Reason", "By", "At"],
               [[a["question_id"], a["original_score"], a["score"], a["reason"], a["by"], a["at"]] for a in m["adjustments"]],
               [12, 14, 14, 70, 22, 24], wrap={4})

    s.cell(r, 1, "Evidence and validation").font = _f(True, "1F3864", 12)
    r += 1
    put("Scored questions", f'=COUNTIF(Traceability!$F$2:$F${n_q + 1},"scored")', formula=True)
    first = r - 1
    put("Scored questions with evidence", f"=COUNTIF(Traceability!$K$2:$K${n_q + 1},TRUE)", formula=True)
    put("Share with evidence", f"=IFERROR(B{r - 1}/B{first},0)", formula=True)
    s.cell(r - 1, 2).number_format = "0.0%"
    s.cell(r - 1, 2).alignment = Alignment(horizontal="left")
    put("Items to validate", f"=COUNTA('To validate'!$A$2:$A${max(n_v, 1) + 1})" if n_v else 0, formula=True)
    put("Items still open", f"=COUNTIF('To validate'!$E$2:$E${n_v + 1},\"open\")" if n_v else 0, formula=True)
    r += 1
    s.cell(r, 1, "Executive summary").font = _f(True, "1F3864", 12)
    r += 1
    put(f"Text ({m['summary']['source']})", m["summary"]["text"])
    s.row_dimensions[r - 1].height = 75
    for line in ["Scores and colours are calculated by code from the agreed ratings (plain mean per group, as in the AWS tool). "
                 "A model never sets or changes a score.",
                 "A blank rating makes its groups Incomplete. The AWS tool would show 0 there; this report does not."]:
        s.cell(r, 2, line).font = _f()
        s.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        s.row_dimensions[r].height = 30
        r += 1
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
