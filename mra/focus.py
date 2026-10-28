"""Rules that turn scores into the short lists and sentences the AWS deck shows (areas of focus, strengths, gaps).

All of these were reverse-engineered from the reference engagement's deck and reproduce it exactly. They are plain code, so the
wording and the choices are the same on every run and can be checked against the numbers.

Tie-break rule (verified on three lists): sort ascending by score, keeping the questionnaire order for equal scores.
The weakest are taken from the front; the strongest from the back, so among equal scores the LAST one in the
questionnaire order counts as the strongest.
"""
from __future__ import annotations

P_STRONG = "You're already doing very well in the {p} dimension; your definitive strength lies in {c}, where you're already well-prepared."
P_WEAK = "We estimate the most initial effort in the {p} dimension; we should especially focus on {c} and improve your capabilities."
P_NEXT = "In addition to that, in the {p} dimension, {c} should receive some attention as well; so let's get started!"
W_STRONG = "You're already excelling in the {w} dimension. Keep up the great work!"
W_WEAK = "Put emphasis on improving the {w} dimension to ensure you're well-prepared for a steady and successful cloud migration."
W_NEXT = "Furthermore, be deliberate in boosting your capabilities in the {w} dimension."


def ascending(items: list[dict]) -> list[dict]:
    """Complete items only, lowest score first, questionnaire order kept for equal scores."""
    done = [(i, it) for i, it in enumerate(items) if it["status"] == "complete"]
    return [it for _, it in sorted(done, key=lambda t: (t[1]["score"], t[0]))]


def weakest(items: list[dict], n: int) -> list[dict]:
    return ascending(items)[:n]


def strongest(items: list[dict], n: int) -> list[dict]:
    asc = ascending(items)
    return list(reversed(asc[-n:])) if n else []


def compute_focus(groups: dict, cfg: dict) -> dict:
    pr = cfg["rubric"]["presentation"]
    cats = groups["categories"]
    acts = groups["activities"]

    def best_cat(pid: str, strong: bool) -> str | None:
        rows = [c for c in cats if c["perspective"] == pid]
        pick = strongest(rows, 1) if strong else weakest(rows, 1)
        return pick[0]["name"] if pick else None

    persp = groups["perspectives"]
    top, low = strongest(persp, 1), weakest(persp, 2)
    p_sent = []
    if top:
        p_sent.append(P_STRONG.format(p=top[0]["name"], c=best_cat(top[0]["id"], True)))
    if low:
        p_sent.append(P_WEAK.format(p=low[0]["name"], c=best_cat(low[0]["id"], False)))
    if len(low) > 1:
        p_sent.append(P_NEXT.format(p=low[1]["name"], c=best_cat(low[1]["id"], False)))
    ws = groups["workstreams"]
    wtop, wlow = strongest(ws, 1), weakest(ws, 2)
    w_sent = []
    if wtop:
        w_sent.append(W_STRONG.format(w=wtop[0]["name"]))
    if wlow:
        w_sent.append(W_WEAK.format(w=wlow[0]["name"]))
    if len(wlow) > 1:
        w_sent.append(W_NEXT.format(w=wlow[1]["name"]))
    pre = [a for a in acts if a["phase"] == "Pre-Mobilize"]
    pages = []
    for page in pr["key_observation_pages"]:
        rows = [a for a in acts if a["workstream"] in page]
        pages.append({"workstreams": list(page), "strong": [a["name"] for a in strongest(rows, pr["key_strong_n"])],
                      "weak": [a["name"] for a in weakest(rows, pr["key_weak_n"])]})
    return {"perspective_sentences": p_sent, "workstream_sentences": w_sent,
            "strengths": [a["name"] for a in strongest(pre, pr["strengths_n"])],
            "gaps": [a["name"] for a in weakest(pre, pr["gaps_n"])], "key_observations": pages,
            "incomplete": [g["name"] for g in persp + ws if g["status"] == "incomplete"]}
