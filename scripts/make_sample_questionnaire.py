"""Write samples/sample_questionnaire.xlsx: a SYNTHETIC filled-in questionnaire in the AWS layout (no customer data).

Ratings are a fixed pattern, with Q42 left blank so the demo shows the Incomplete handling. Run from the project root.
"""
from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mra.loader import load_config  # noqa: E402

TEXT = {1: "Synthetic sample: about 200 servers, mostly Windows, in scope. Network devices are out of scope.",
        2: "Reduce risk on unsupported platforms and cut licensing cost.",
        3: "About 200 servers and 120 applications.", 4: "Yes, SOC 2.", 5: "Yes",
        17: "A cloud-first approach, migrating in phases.", 18: "Accelerate at scale.", 19: "No friction expected.",
        20: "Executive sponsorship and a cross-functional team.", 21: "Yes: infrastructure, operating systems and platforms.",
        22: "There is no cloud team."}
YESNO = {7: "Yes", 8: "Yes", 9: "Yes", 10: "No", 11: "Yes", 12: "No", 13: "No", 14: "No", 15: "No", 16: "No"}


def build(out: Path) -> Path:
    cfg = load_config(ROOT / "config")
    wb = Workbook()
    ws = wb.active
    ws.title = "Questions"
    ws.append(["Section", "Category", "#", "Question", "Response Type", "Response", "Rating Guide (if applicable)", "Comments",
               "Rating 1", "Rating 2", "Rating 3", "Rating 4", "Rating 5"])
    for q in sorted(cfg["questions"], key=lambda x: x.number):
        lv = list(q.levels) + [None] * (5 - len(q.levels))
        resp = guide = comm = None
        if q.type == "rating" and q.perspective and q.id != "Q42":
            resp = 2 + (q.number * 7) % 4                      # 2 to 5, a fixed pattern
            guide = q.levels[resp - 1]
        elif q.type == "yes_no":
            resp = YESNO.get(q.number)
        elif q.type == "text":
            comm = TEXT.get(q.number)
        ws.append([q.section, q.category, q.number, q.text, {"rating": "Rating", "yes_no": "Yes/No", "text": "Text"}[q.type], resp, guide, comm, *lv])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


if __name__ == "__main__":
    print("Wrote", build(ROOT / "samples" / "sample_questionnaire.xlsx"))
