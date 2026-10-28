"""The checker. A model may describe the facts it is given; it may never change them.

Every reason returned is plain text that is fed back to the model on a retry and written to the audit log.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

COLOUR_WORDS = ("red", "amber", "yellow", "green")
ALIAS = {"yellow": "amber"}
_NUM = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(obj) -> set[float]:
    """Every number stored as a number (not inside text) anywhere in the facts."""
    out: set[float] = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= numbers_in(v)
    return out


def texts_in(obj) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from texts_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from texts_in(v)


def id_regex(ids: Iterable[str]) -> re.Pattern | None:
    ids = sorted({i for i in ids if i}, key=len, reverse=True)
    if not ids:
        return None
    return re.compile(r"(?<![A-Za-z0-9_.-])(" + "|".join(re.escape(i) for i in ids) + r")(?![A-Za-z0-9_-]|\.\d)")


def _check_text(text: str, field: str, limit: int, facts: dict, allowed_ids: set[str], all_ids_rx: re.Pattern | None,
                forbidden: list[re.Pattern], allowed_colours: set[str], reasons: list[str]) -> None:
    if not isinstance(text, str) or not text.strip():
        reasons.append(f"{field} is missing or empty")
        return
    if len(text) > limit:
        reasons.append(f"{field} is {len(text)} characters; the limit is {limit}")
    stripped = text
    if all_ids_rx is not None:
        for m in all_ids_rx.finditer(text):
            if m.group(1) not in allowed_ids:
                reasons.append(f"{field} mentions '{m.group(1)}', which is not part of this item")
        stripped = all_ids_rx.sub(" ", text)
    allowed_nums = numbers_in(facts) | {float(n) for t in texts_in(facts) for n in _NUM.findall(t)}
    for n in _NUM.findall(stripped):
        if not any(abs(float(n) - a) < 1e-9 for a in allowed_nums):
            reasons.append(f"{field} states the number {n}, which is not in the facts")
    for word in re.findall(r"\b(" + "|".join(COLOUR_WORDS) + r")\b", text, flags=re.I):
        if ALIAS.get(word.lower(), word.lower()) not in allowed_colours:
            reasons.append(f"{field} says '{word}', but this item is {', '.join(sorted(allowed_colours)) or 'not coloured'}")
    for rx in forbidden:
        if rx.search(text):
            reasons.append(f"{field} claims an action was completed ('{rx.search(text).group(0)}')")


def check_activity_output(facts: dict, out: dict, limits: dict, forbidden: list[re.Pattern],
                          all_ids_rx: re.Pattern | None) -> list[str]:
    reasons: list[str] = []
    if not isinstance(out, dict):
        return ["item is not a JSON object"]
    evidence_ids = {e["id"] for q in facts["questions"] for e in q["evidence"]}     # only what this activity was given
    allowed_ids = {facts["activity"]} | {q["id"] for q in facts["questions"]} | evidence_ids
    used = out.get("evidence_used", [])
    if not isinstance(used, list) or not all(isinstance(x, str) for x in used):
        reasons.append("evidence_used must be a list of evidence ids")
    else:
        for x in used:
            if x not in evidence_ids:
                reasons.append(f"evidence_used lists '{x}', which was not provided for this activity")
    colours = {facts["colour"]}
    for field, key in (("observation", "observation_chars"), ("next_step", "next_step_chars")):
        _check_text(out.get(field), field, limits[key], facts, allowed_ids, all_ids_rx, forbidden, colours, reasons)
    return reasons


def check_summary_output(facts: dict, out: dict, limits: dict, forbidden: list[re.Pattern],
                         all_ids_rx: re.Pattern | None) -> list[str]:
    reasons: list[str] = []
    if not isinstance(out, dict):
        return ["summary is not a JSON object"]
    _check_text(out.get("summary"), "summary", limits["summary_chars"], facts, set(), all_ids_rx, forbidden,
                {"red", "amber", "green"}, reasons)
    return reasons
