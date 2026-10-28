"""Validation items, activity observations and actions, the evidence traceability summary, and the CSV files.

Rule: every scored question is either evidenced or appears on the validation list. Nothing is called traceable when it is not.
"""
from __future__ import annotations

import csv
import io
import re

ACTIVITY_FIELDS = ("id", "name", "workstream", "phase", "score", "mean", "radar", "aws_equivalent", "colour", "status", "answered", "total")
KIND_ORDER = ["blank_rating", "guide_mismatch", "unverified_high_claim", "no_evidence"]
_LEVEL_PREFIX = re.compile(r"^\s*[1-5]\s*[-\u2013\u2014:.]\s*")


def strip_level(text: str | None) -> str:
    return _LEVEL_PREFIX.sub("", text or "").strip()


def build_validation_items(qresults: list[dict], rubric: dict) -> list[dict]:
    items: list[dict] = []
    for q in qresults:
        if q["perspective"] is None:
            continue                                   # optional modules never block a report
        qid = q["question_id"]
        if q["status"] == "unanswered":
            items.append({"kind": "blank_rating", "ref": qid, "refs": [], "validate_with": [],
                          "reason": f"{qid} ({q['category']}) has no rating, so every group it belongs to shows as Incomplete."})
            continue
        if q["guide_mismatch"]:
            items.append({"kind": "guide_mismatch", "ref": qid, "refs": [], "validate_with": [],
                          "reason": f"{qid}: the rating is {q['original_score']} but the Rating Guide text shows level {q['guide_level']}."})
        if not q["traceable"]:
            if q["score"] >= rubric["high_claim_at_or_above"]:
                kind, why = "unverified_high_claim", f"{qid} is rated {q['score']} with no supporting evidence."
            else:
                kind, why = "no_evidence", f"{qid} is rated {q['score']} with no supporting evidence."
            items.append({"kind": kind, "ref": qid, "refs": [], "reason": why, "validate_with": []})
    items.sort(key=lambda i: (KIND_ORDER.index(i["kind"]), int(i["ref"][1:])))
    for n, it in enumerate(items, 1):
        it["item_id"] = f"V{n:03d}"
    return items


def build_activities(act_scores: list[dict], qresults: list[dict], cfg: dict) -> list[dict]:
    """Per readiness activity: a factual observation from the rating-level descriptions, and baseline actions.

    The wording here is the questionnaire's own. A model may rewrite it later (labelled and checked), but this
    is what stands when no model is used.
    """
    qby = {q["question_id"]: q for q in qresults}
    lib = cfg["actions"]
    below = cfg["rubric"]["action_below"]
    out = []
    for a in act_scores:
        qs = [qby[i] for i in a["question_ids"]]
        weak = [q for q in qs if q["score"] is not None and q["score"] < below]
        needs = a["status"] == "complete" and a["score"] < below
        texts = []
        for q in qs:
            t = strip_level(q["level_text"])
            if t and t not in texts:
                texts.append(t)
        obs = " ".join(texts) if texts else "No rating has been given for this activity yet."
        actions = []
        if needs:
            for entry in lib["actions"]:
                if a["id"] in entry["activities"]:
                    actions.append({"source": "library", "library_id": entry["id"], "objective": entry["objective"], "text": entry["text"]})
            if not actions:
                lowest = min(weak, key=lambda q: (q["score"], q["number"]))
                nxt = strip_level(lowest["next_level_text"])
                txt = (f"{lib['fallback']} For {lowest['question_id']}, the next level reads: {nxt}" if nxt else lib["fallback"])
                actions.append({"source": "next_level", "library_id": None, "objective": None, "text": txt})
        for i, ac in enumerate(actions, 1):
            ac.update({"id": f"{a['id']}-A{i}", "owner": None, "due_date": None, "priority": None, "effort": None,
                       "benefit": None, "status": "unassigned"})
        out.append({**{k: a[k] for k in ACTIVITY_FIELDS},
                    "needs_action": needs, "weak_questions": [q["question_id"] for q in weak],
                    "question_ids": a["question_ids"], "observation": {"text": obs, "source": "tool"},
                    "actions": actions, "evidence_ids": sorted({e for q in qs for e in q["evidence_ids"]})})
    return out


def traceability_summary(qresults: list[dict], items: list[dict]) -> dict:
    scored = [q for q in qresults if q["perspective"] is not None and q["status"] == "scored"]
    evidenced = [q for q in scored if q["traceable"]]
    listed = {i["ref"] for i in items if i["kind"] in ("no_evidence", "unverified_high_claim")}
    unaccounted = [q["question_id"] for q in scored if not q["traceable"] and q["question_id"] not in listed]
    return {"scored_questions": len(scored), "evidenced": len(evidenced),
            "evidenced_pct": round(100 * len(evidenced) / len(scored), 1) if scored else None,
            "scored_without_evidence_listed": len(scored) - len(evidenced), "unaccounted_for": unaccounted}


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _cell(v):
    return ";".join(map(str, v)) if isinstance(v, list) else ("" if v is None else v)


def safe_cell(v):
    """Spreadsheet programs run a cell that starts with = + - @ as a formula. Prefix those with a quote (F64)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def matrix_csv(qresults: list[dict]) -> str:
    cols = ["question_id", "number", "section", "category", "activity", "status", "score", "original_score", "level_text",
            "evidence_status", "traceable", "evidence_ids", "link_quotes", "guide_level", "adjusted"]
    return _csv(cols, [[safe_cell(_cell(q[c])) for c in cols] for q in qresults])


def scores_csv(groups: dict) -> str:
    rows = []
    for kind in ("perspectives", "workstreams", "activities", "categories"):
        for g in groups[kind]:
            rows.append([kind[:-1] if kind != "categories" else "category", g["id"], g["name"], _cell(g["score"]), g["status"],
                         g["colour"], _cell(g["radar"]), g["answered"], g["total"]])
    return _csv(["level", "id", "name", "score", "status", "colour", "radar", "answered", "total"], [[safe_cell(c) for c in r] for r in rows])


def validation_csv(items: list[dict]) -> str:
    return _csv(["item_id", "kind", "ref", "reason"], [[i["item_id"], i["kind"], i["ref"], safe_cell(i["reason"])] for i in items])
