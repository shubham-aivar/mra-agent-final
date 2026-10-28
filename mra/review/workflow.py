"""Review decisions and the release gate. Pure functions on a plain dict, so they are easy to test.

A Technical Consultant must: give every blank rating a value, decide on the actions of every activity that needs
them, resolve every item on the validation list, confirm the scores, and fill in the report details. Only then can
the report be finalised. A confirmation is tied to the exact scores it covered (by fingerprint): if the scores
change afterwards it no longer counts.
"""
from __future__ import annotations

from datetime import date

DECISIONS = ("approve", "edit", "reject")
RESOLUTIONS = ("resolved", "accepted_risk", "not_applicable")
REQUIRED_META = ("facilitator", "workshop_date", "readout_date")
DATE_META = ("workshop_date", "readout_date")


class ReviewLocked(Exception):
    pass


class ReleaseBlocked(Exception):
    def __init__(self, blockers: list[str]):
        self.blockers = blockers
        super().__init__("; ".join(blockers))


def new_state(set_id: str) -> dict:
    return {"set_id": set_id, "decisions": {}, "resolutions": {}, "overrides": {}, "meta": {}, "scores_confirmed": None, "final": None}


def item_key(item: dict) -> str:
    return f"{item['kind']}:{item['ref']}"


def ensure_unlocked(state: dict) -> None:
    if state["final"]:
        raise ReviewLocked("the report is finalised; reopen it (with a reason) before changing anything")


def _valid_date(v: str) -> bool:
    try:
        date.fromisoformat(v)
        return True
    except (TypeError, ValueError):
        return False


def set_decision(state: dict, result: dict, activity: str, decision: str, by: str, at: str, text: str | None = None,
                 owner: str | None = None, due_date: str | None = None, comment: str | None = None) -> None:
    ensure_unlocked(state)
    if activity not in {a["id"] for a in result["activities"] if a["needs_action"]}:
        raise ValueError(f"'{activity}' is not an activity that needs an action, so it has nothing to decide on")
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if decision == "edit" and not (text or "").strip():
        raise ValueError("an edited action needs the new text")
    if decision == "reject" and not (comment or "").strip():
        raise ValueError("rejecting an action needs a comment")
    if due_date is not None and not _valid_date(due_date):
        raise ValueError("due date must look like 2026-12-31")
    prev = state["decisions"].get(activity, {})
    score = next(a["score"] for a in result["activities"] if a["id"] == activity)
    state["decisions"][activity] = {
        "decision": decision, "score": score, "text": (text or prev.get("text") or "").strip() or None,
        "owner": (owner or prev.get("owner") or "").strip() or None, "due_date": due_date or prev.get("due_date"),
        "comment": (comment or "").strip() or None, "by": by, "at": at}


def set_resolution(state: dict, result: dict, key: str, resolution: str, note: str, by: str, at: str) -> None:
    ensure_unlocked(state)
    if key not in {item_key(i) for i in result["validation_items"]}:
        raise ValueError(f"no validation item '{key}'")
    if key.startswith("blank_rating:"):
        raise ValueError("a blank rating is not resolved with a note: give it a rating (adjust, or accept an agent rating proposal)")
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}")
    if not (note or "").strip():
        raise ValueError("a resolution needs a note")
    reason = next(i["reason"] for i in result["validation_items"] if item_key(i) == key)
    state["resolutions"][key] = {"resolution": resolution, "note": note.strip(), "reason": reason, "by": by, "at": at}


def set_meta(state: dict, values: dict, by: str, at: str) -> dict:
    ensure_unlocked(state)
    from ..loader import META_FIELDS
    bad = sorted(set(values) - set(META_FIELDS))
    if bad:
        raise ValueError(f"unknown report detail(s) {bad}; allowed: {list(META_FIELDS)}")
    for k in DATE_META:
        if values.get(k) and not _valid_date(values[k]):
            raise ValueError(f"{k} must look like 2026-12-31")
    clean = {k: str(v).strip() for k, v in values.items() if v not in (None, "")}
    state["meta"] = {**state.get("meta", {}), **clean}
    return state["meta"]


def effective_meta(result: dict, state: dict) -> dict:
    """Report details: those given with the input, overridden by those set during review."""
    return {**result["meta"].get("report", {}), **state.get("meta", {})}


def gate(result: dict, state: dict) -> list[str]:
    """Everything still standing between this report and release. Empty means it can be finalised."""
    blockers = []
    if result["blank_ratings"]:
        shown = ", ".join(result["blank_ratings"][:8]) + (" ..." if len(result["blank_ratings"]) > 8 else "")
        blockers.append(f"{len(result['blank_ratings'])} rating(s) are blank ({shown}): give each a rating "
                        "(python -m mra adjust, or accept an agent rating proposal)")
    for a in result["activities"]:
        if not a["needs_action"]:
            continue
        d = state["decisions"].get(a["id"])
        if not d:
            blockers.append(f"activity {a['id']} ({a['name']}, {a['colour']}): no decision on the action yet")
        elif d["decision"] in ("approve", "edit"):
            if not d["owner"]:
                blockers.append(f"activity {a['id']}: the approved action has no owner")
            if not d["due_date"]:
                blockers.append(f"activity {a['id']}: the approved action has no due date")
    for i in result["validation_items"]:
        if i["kind"] != "blank_rating" and item_key(i) not in state["resolutions"]:
            blockers.append(f"validation item {item_key(i)}: not resolved")
    conf = state["scores_confirmed"]
    if not conf:
        blockers.append("the reviewer has not confirmed the scores")
    elif conf.get("fingerprint") != result["meta"]["scores_fingerprint"]:
        blockers.append("the scores changed after they were confirmed: confirm them again")
    meta = effective_meta(result, state)
    missing = [k for k in REQUIRED_META if not meta.get(k)]
    if missing:
        blockers.append(f"report details missing: {', '.join(missing)} (python -m mra meta)")
    return blockers


def reconcile(state: dict, result: dict) -> list[str]:
    """After a new run, drop review records that no longer describe the data (a decision for an activity whose score
    changed, a resolution of an item whose wording changed or that is gone). Returns what was dropped, for the audit log."""
    dropped = []
    acts = {a["id"]: a for a in result["activities"]}
    for aid in list(state["decisions"]):
        a, d = acts.get(aid), state["decisions"][aid]
        if a is None or not a["needs_action"] or d.get("score") != a["score"]:
            dropped.append(f"decision:{aid}")
            del state["decisions"][aid]
    items = {item_key(i): i for i in result["validation_items"]}
    for key in list(state["resolutions"]):
        i, r = items.get(key), state["resolutions"][key]
        if i is None or r.get("reason") != i["reason"]:
            dropped.append(f"resolution:{key}")
            del state["resolutions"][key]
    return dropped
