"""Pure computation: a response set in, scored result out. No model, no storage, no clock."""
from __future__ import annotations

from .focus import compute_focus
from .loader import canonical, sha256
from .models import ResponseSet
from .scoring import score_groups, score_questions
from .traceability import build_activities, build_validation_items, traceability_summary


def scores_fingerprint(result: dict) -> str:
    """A short hash of everything a reviewer confirms: every rating, every group score, and the evidence picture.
    It does not change when model-written text or timestamps change, so a confirmation stays valid exactly as long
    as the numbers it covered do."""
    body = {"q": [(q["question_id"], q["score"]) for q in result["questions"]],
            "g": [(g["id"], g["score"], g["status"]) for k in ("perspectives", "workstreams", "activities", "categories", "modules")
                  for g in result[k]],
            "t": [result["traceability"]["scored_questions"], result["traceability"]["evidenced"]]}
    return sha256(canonical(body))[:16]


def text_answers(rs: ResponseSet, cfg: dict) -> tuple[list[dict], list[dict]]:
    given = {a.question_id: a.value for a in rs.answers}
    gates = {m["id"]: given.get(m["gate"], "") for m in cfg["modules"]}
    enabled = {mid: v.strip().lower() == "yes" for mid, v in gates.items()}
    rows = []
    for q in cfg["questions"]:
        if q.type == "rating":
            continue
        rows.append({"question_id": q.id, "number": q.number, "section": q.section, "category": q.category, "type": q.type,
                     "question": q.text, "answer": given.get(q.id, ""), "module": q.module,
                     "included": q.module is None or enabled.get(q.module, False)})
    mods = [{"id": m["id"], "name": m["name"], "gate": m["gate"], "gate_answer": gates[m["id"]], "enabled": enabled[m["id"]]}
            for m in cfg["modules"]]
    return rows, mods


def compute_result(rs: ResponseSet, cfg: dict, overrides: dict | None = None, links: list[dict] | None = None) -> dict:
    act_by_id = {a["id"]: a for a in cfg["activities"]}
    qres = score_questions(cfg["questions"], rs.ratings, rs.evidence, cfg["activity_of"], act_by_id, overrides, links)
    groups = score_groups(qres, cfg)
    items = build_validation_items(qres, cfg["rubric"])
    answers, mods = text_answers(rs, cfg)
    result = {
        "meta": {"set_id": rs.set_id, "customer_label": rs.customer_label, "config_hash": cfg["config_hash"],
                 "versions": cfg["versions"], "placeholder_notes": cfg["placeholder_notes"], "agent_links": len(links or []),
                 "input_warnings": list(rs.warnings), "report": dict(rs.meta),
                 "scope_note": "Scores show readiness for cloud migration. They are not a rating of the current on-premises environment."},
        "perspectives": groups["perspectives"], "workstreams": groups["workstreams"], "categories": groups["categories"],
        "activities": build_activities(groups["activities"], qres, cfg), "modules": groups["modules"],
        "questions": qres, "text_answers": answers, "optional_modules": mods, "validation_items": items,
        "traceability": traceability_summary(qres, items), "focus": compute_focus(groups, cfg),
        "adjustments": [{"question_id": q["question_id"], "original_score": q["original_score"], "score": q["score"],
                         **q["adjustment"]} for q in qres if q["adjusted"]],
        "blank_ratings": [q["question_id"] for q in qres if q["perspective"] and q["status"] == "unanswered"],
    }
    result["meta"]["scores_fingerprint"] = scores_fingerprint(result)
    return result
