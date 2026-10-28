"""One merged, render-ready view of a result and its review. The Excel, Word and PowerPoint writers only read this."""
from __future__ import annotations

from .review.workflow import REQUIRED_META, effective_meta, item_key


def _group_label(g: dict) -> str:
    return "Incomplete" if g["status"] != "complete" else f"{g['score']:.1f}"


def _blank_action(a: dict) -> dict:
    return {"id": f"{a['id']}-A1", "source": "tool", "library_id": None, "objective": None, "text": "", "owner": None,
            "due_date": None, "priority": None, "effort": None, "benefit": None, "status": "unassigned"}


def build_report_model(result: dict, state: dict, generated_at: str, proposals: list[dict] | None, cfg: dict) -> dict:
    final = state.get("final")
    exp = result.get("explanations", {})
    meta = effective_meta(result, state)
    acts, action_rows, rejected = [], [], []
    for a in result["activities"]:
        e, d = exp.get(a["id"], {}), state["decisions"].get(a["id"])
        obs = e.get("observation") or a["observation"]
        actions = [dict(x) for x in a["actions"]]
        nxt = e.get("next_step")
        edited = bool(d and d["decision"] == "edit" and d["text"])
        if a["needs_action"] and nxt and not edited:
            # precedence for the wording of the first action: reviewer edit > model text > the library or next-level text
            actions = [{**(actions[0] if actions else _blank_action(a)), "text": nxt["text"], "source": nxt["source"]}] + actions[1:]
        if edited:
            actions = [{**(actions[0] if actions else _blank_action(a)), "text": d["text"], "source": "reviewer"}]
        status = "Proposed" if d is None else {"approve": "Approved", "edit": "Approved (edited)", "reject": "Rejected"}[d["decision"]]
        for x in actions:
            x["owner"] = d["owner"] if d else None
            x["due_date"] = d["due_date"] if d else None
            x["status"] = status
        row = {**{k: a[k] for k in ("id", "name", "workstream", "phase", "score", "colour", "status", "answered", "total",
                                   "needs_action", "weak_questions", "question_ids")},
               "workstream_name": next(w["name"] for w in result["workstreams"] if w["id"] == a["workstream"]),
               "observation": obs, "actions": actions, "decision": d["decision"] if d else ("none needed" if not a["needs_action"] else "pending"),
               "comment": d["comment"] if d else None, "evidence_used": e.get("evidence_used", [])}
        acts.append(row)
        if d and d["decision"] == "reject":
            rejected.append(row)
        elif a["needs_action"]:
            action_rows += [{"activity": row, "action": x} for x in actions]
    accepted = [p for p in proposals or [] if p["status"] == "accepted"]
    followups = {p["payload"]["item_key"]: p["payload"] for p in accepted if p["kind"] == "followup"}
    context_items = [{**p["payload"], "id": p["id"]} for p in accepted if p["kind"] == "context_item"]
    items = []
    for i in result["validation_items"]:
        r = state["resolutions"].get(item_key(i))
        items.append({**i, "key": item_key(i), "resolution": r["resolution"] if r else "open", "note": r["note"] if r else "",
                      "followup": followups.get(item_key(i))})
    llm = result["meta"].get("llm", {})
    return {
        "title": f"Migration Readiness Assessment: {result['meta']['customer_label']}",
        "customer": result["meta"]["customer_label"], "set_id": result["meta"]["set_id"],
        "status": "FINAL" if final else "DRAFT",
        "status_note": (f"Approved by {final['by']} on {final['at']}." if final else
                        "DRAFT. A Technical Consultant must review and finalise this report before it is released."),
        "generated_at": generated_at, "scope_note": result["meta"]["scope_note"], "versions": result["meta"]["versions"],
        "config_hash": result["meta"]["config_hash"], "placeholder_notes": result["meta"]["placeholder_notes"],
        "engine": llm.get("engine", "none"), "details": meta, "details_missing": [k for k in REQUIRED_META if not meta.get(k)],
        "summary": result.get("narrative") or {"text": "", "source": "code"},
        "perspectives": result["perspectives"], "workstreams": result["workstreams"], "categories": result["categories"],
        "modules": result["modules"], "activities": acts, "action_rows": action_rows, "rejected_actions": rejected,
        "focus": result["focus"], "questions": result["questions"], "text_answers": result["text_answers"],
        "optional_modules": result["optional_modules"], "validation": items, "traceability": result["traceability"],
        "adjustments": result["adjustments"], "blank_ratings": result["blank_ratings"], "context_items": context_items,
        "scores_confirmed": state.get("scores_confirmed"), "radar_guide": cfg["rubric"]["radar_guide"],
        "areas": list(cfg["areas"]), "question_bank": cfg["qmap"], "input_warnings": result["meta"].get("input_warnings", []),
        "pending_proposals": sum(p["status"] == "proposed" for p in proposals or []),
    }
