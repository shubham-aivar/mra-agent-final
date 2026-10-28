"""All scoring is plain code. A model never produces or changes a score.

The rules reproduce the AWS MRA tool as seen in the reference engagement's outputs (every Scores-sheet value and every chart value):

* A question has one agreed rating, 1 to 5.
* A group (category, perspective, activity, workstream, optional module) scores the plain mean of the ratings of
  the questions in it. Every question weighs the same, so a perspective is the mean of its questions, not the mean
  of its categories.
* Display values are rounded half up to one decimal (3.25 shows 3.3). Radar values are the mean rounded half up to
  three decimals, times 20, rounded to two decimals (63.34, not 63.33).
* The AWS tool shows 0 for any group that contains a blank rating. That is wrong as a readiness statement, so here
  such a group is Incomplete: no score, no colour, and release is blocked until the rating is set. The number the
  AWS tool would show is kept as `aws_equivalent` for back-testing only.
* Colour: green from the configured level (4.0), red below the configured red level (unknown, so null), amber otherwise.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import Evidence, Question, Rating


def round_half_up(x: float, decimals: int) -> float:
    """Round half up on the shortest decimal form, so 3.25 -> 3.3 and not the binary-float 3.2."""
    return float(Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))


def colour_for(score: float | None, rubric: dict) -> str:
    if score is None:
        return "incomplete"
    c = rubric["colours"]
    if score >= c["green_from"]:
        return "green"
    if c.get("red_below") is not None and score < c["red_below"]:
        return "red"
    return "amber"


def level_text(q: Question, score: int | None) -> str | None:
    return q.levels[score - 1] if score is not None and q.levels else None


def score_questions(questions: tuple[Question, ...], ratings: tuple[Rating, ...], evidence: tuple[Evidence, ...],
                    activity_of: dict[str, str], act_by_id: dict[str, dict], overrides: dict | None = None,
                    links: list[dict] | None = None) -> list[dict]:
    """One result per rating question. A reviewer adjustment replaces the rating (the original is kept)."""
    overrides = overrides or {}
    link_ids: dict[str, set[str]] = {}
    link_quotes: dict[str, list[str]] = {}
    for lk in links or []:
        link_ids.setdefault(lk["question_id"], set()).add(lk["evidence_id"])
        link_quotes.setdefault(lk["question_id"], []).append(f"{lk['evidence_id']}: {lk['quote']}")
    given = {r.question_id: r for r in ratings}
    tagged: dict[str, set[str]] = {}
    for e in evidence:
        for t in e.applies_to:
            tagged.setdefault(t, set()).add(e.evidence_id)
    out = []
    for q in questions:
        if q.type != "rating":
            continue
        r, ov = given.get(q.id), overrides.get(q.id)
        act = act_by_id.get(activity_of.get(q.id, ""))
        evids = sorted(tagged.get(q.id, set()) | tagged.get(q.area, set()) | tagged.get(act["id"] if act else "", set())
                       | link_ids.get(q.id, set()))
        original = r.score if r else None
        value = ov["score"] if ov else original
        base = {"question_id": q.id, "number": q.number, "section": q.section, "category": q.category, "area": q.area,
                "perspective": q.perspective, "module": q.module, "activity": act["id"] if act else None,
                "workstream": act["workstream"] if act else None, "score": value, "original_score": original,
                "status": "scored" if value is not None else "unanswered", "level_text": level_text(q, value),
                "next_level_text": level_text(q, value + 1) if value is not None and value < len(q.levels) else None,
                "guide_level": r.guide_level if r else None,
                "guide_mismatch": bool(r and r.guide_level is not None and r.guide_level != r.score and not ov),
                "evidence_ids": evids, "link_quotes": sorted(link_quotes.get(q.id, [])),
                "evidence_status": "none", "traceable": False,
                "adjusted": ov is not None, "adjustment": ({k: ov[k] for k in ("reason", "by", "at")} if ov else None),
                "comment": r.comment if r else ""}
        if value is not None:
            base["evidence_status"] = "evidenced" if evids else "self_reported"
            base["traceable"] = bool(evids)
        out.append(base)
    return out


def rollup(results: list[dict], rubric: dict) -> dict:
    """Score one group of question results."""
    rd = rubric["rounding"]
    total = len(results)
    vals = [r["score"] for r in results if r["score"] is not None]
    answered = len(vals)
    if total == 0 or answered < total:
        return {"status": "incomplete", "score": None, "mean": None, "radar": None, "colour": "incomplete",
                "answered": answered, "total": total, "aws_equivalent": 0.0}
    mean = round_half_up(sum(vals) / total, rd["internal_decimals"])
    score = round_half_up(sum(vals) / total, rd["display_decimals"])
    return {"status": "complete", "score": score, "mean": mean,
            "radar": round_half_up(mean * rd["radar_multiplier"], rd["radar_decimals"]),
            "colour": colour_for(score, rubric), "answered": answered, "total": total, "aws_equivalent": score}


def score_groups(qresults: list[dict], cfg: dict) -> dict:
    """Categories, perspectives, activities, workstreams and optional modules, all from the same question results."""
    rub = cfg["rubric"]
    core = [q for q in qresults if q["perspective"] is not None]
    by = lambda rows, key: {k: [r for r in rows if r[key] == k] for k in dict.fromkeys(r[key] for r in rows)}  # noqa: E731

    categories = []
    cat_rows = by(core, "area")
    for a in cfg["areas"]:
        if a["id"] in cat_rows:
            categories.append({"id": a["id"], "name": a["name"], "perspective": a["perspective"],
                               **rollup(cat_rows[a["id"]], rub)})
    persp_rows = by(core, "perspective")
    perspectives = [{"id": p["id"], "name": p["name"], **rollup(persp_rows.get(p["id"], []), rub)} for p in cfg["perspectives"]]
    act_rows = by([q for q in core if q["activity"]], "activity")
    activities = []
    for a in cfg["activities"]:
        res = rollup(act_rows.get(a["id"], []), rub)
        activities.append({"id": a["id"], "name": a["name"], "workstream": a["workstream"], "phase": a["phase"],
                           "question_ids": a["questions"], **res})
    ws_rows = by([q for q in core if q["workstream"]], "workstream")
    workstreams = [{"id": w["id"], "name": w["name"], "activity_ids": w["activities"],
                    **rollup(ws_rows.get(w["id"], []), rub)} for w in cfg["workstreams"]]
    mod_rows = by([q for q in qresults if q["module"]], "module")
    modules = [{"id": m["id"], "name": m["name"], **rollup(mod_rows[m["id"]], rub)} for m in cfg["modules"] if m["id"] in mod_rows]
    return {"categories": categories, "perspectives": perspectives, "activities": activities, "workstreams": workstreams,
            "modules": modules}
