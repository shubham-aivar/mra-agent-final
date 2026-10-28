"""A plain list of what a person still has to do before the deck and report can go to the customer.

Only things the questionnaire cannot supply, or that a person must decide, are listed. Nothing here is guessed.
"""
from __future__ import annotations

from .ingram_deck import INGRAM_LABEL


def render_todo(m: dict, template: bytes | None = None) -> bytes:
    L = [f"# Open items before release: {m['customer']}", "", m["status_note"], ""]
    sec = []
    if m["blank_ratings"]:
        sec.append(("Blank ratings (the groups that contain them show as Incomplete)", [f"{q}" for q in m["blank_ratings"]]))
    if m["details_missing"]:
        sec.append(("Report details to fill in (python -m mra meta)", m["details_missing"]))
    open_items = [v for v in m["validation"] if v["resolution"] == "open" and v["kind"] != "blank_rating"]
    if open_items:
        sec.append((f"Validation items not resolved ({len(open_items)})", [f"{v['key']}: {v['reason']}" for v in open_items[:25]]
                    + ([f"... and {len(open_items) - 25} more"] if len(open_items) > 25 else [])))
    pend = [a for a in m["activities"] if a["decision"] == "pending"]
    if pend:
        sec.append(("Activities whose proposed action is not decided yet", [f"{a['id']} {a['name']}" for a in pend]))
    if m["pending_proposals"]:
        sec.append(("Agent proposals waiting for a decision", [f"{m['pending_proposals']} proposal(s): python -m mra proposals"]))
    blank = m.get("deck_blank_sections")
    if blank:                                                              # the Ingram deck: every section is present, these have no data yet
        sec.append(("Deck sections left blank: they need other inputs (supply them, or complete them by hand)", blank))
    else:
        sec.append(("Deck slides the questionnaire cannot fill (decide or supply)", [
            "Mobilize cost slides (cost breakdown by scope and by role): rates, days and MAP funding come from the proposal, not the MRA",
            "Delivery-model slides (three variants of the Mobilize / EBA journey): keep the one that fits and delete the others",
            "AMS fit score slide: the AWS tool computes it; it shows 0% unless AMS is a recommended action and the assessment is marked complete",
            "Next steps slide: owners and target dates",
            "Prioritized list of actions slide: it is the AWS standard list; delete the rows that do not apply",
            "Company logo on the cover slides"]))
    if m.get("template_name") and m["template_name"] != INGRAM_LABEL:
        ok_line = ["every part of this deck can be filled, as in the built-in Ingram deck"]
        sec.append(("Deck template used: " + m["template_name"], m.get("template_warnings") or ok_line))
    if m.get("deck_warnings"):
        sec.append(("Deck check", m["deck_warnings"]))
    if m["input_warnings"]:
        sec.append(("Questionnaire notes", m["input_warnings"]))
    for head, lines in sec:
        L += [f"## {head}", ""] + [f"- {x}" for x in lines] + [""]
    return ("\n".join(L) + "\n").encode("utf-8")
