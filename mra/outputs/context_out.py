"""A plain-language business-context note for migration planning.

The SOW (A04) says MRA findings (readiness gaps, business priorities, constraints) go to AWS Transform migration
planning as business context, and that AWS Transform accepts free-form notes. This writes that note as Markdown.
Only priorities and constraints a person accepted are included, each with the exact words it came from.
"""
from __future__ import annotations


def render_context(m: dict, template: bytes | None = None) -> bytes:
    L = [f"# Business context for migration planning: {m['customer']}", "", f"Status: {m['status']}. {m['status_note']}", "",
         "Source: Migration Readiness Assessment. " + m["scope_note"], ""]
    for title, kind in (("Business priorities", "priority"), ("Constraints", "constraint")):
        items = [i for i in m["context_items"] if i["kind"] == kind]
        L += [f"## {title}", ""]
        if not items:
            L += ["None confirmed yet.", ""]
        for i in items:
            L += [f"- {i['text']}", f"  Source: {i['evidence_id']}, \"{i['quote']}\""]
        L.append("")
    L += ["## Readiness gaps", ""]
    gaps = [a for a in m["activities"] if a["needs_action"] and a["decision"] != "reject"]
    if not gaps:
        L += ["No activity needs an action.", ""]
    for a in gaps:
        act = a["actions"][0] if a["actions"] else None
        owner = f" Owner: {act['owner']}, due {act['due_date']}." if act and act["owner"] and act["due_date"] else ""
        score = "incomplete" if a["status"] != "complete" else a["score"]
        L += [f"- **{a['name']}** ({a['workstream_name']}, {a['phase']}, rated {a['colour']}, score {score}). "
              f"{a['observation']['text']} Next step: {act['text'] if act else 'to be agreed'}{owner}"]
    L += ["", "## Still being validated", ""]
    open_items = [v for v in m["validation"] if v["resolution"] == "open"]
    L += [f"- {v['reason']}" for v in open_items] or ["Nothing outstanding."]
    return ("\n".join(L) + "\n").encode("utf-8")
