from __future__ import annotations

import json
import re

from ..ports import ChatReply, LlmReply

_FACTS = re.compile(r"<facts>(.*)</facts>", re.S)


class StubLlm:
    """Deterministic stand-in for a model. It reads the facts out of the prompt and writes text that
    passes the checker. Used for local runs and tests; its output is always labelled 'stub'."""
    name = "stub"
    model_id = "stub"

    def complete(self, system: str, user: str, max_tokens: int) -> LlmReply:
        facts = json.loads(_FACTS.search(user).group(1))
        if isinstance(facts, dict):
            done = [p for p in facts["perspectives"] if p["score"] is not None]
            if done:
                best, worst = max(done, key=lambda p: p["score"]), min(done, key=lambda p: p["score"])
                text = (f"The strongest perspective is {best['name']} at {best['score']} out of {facts['scale'][1]}; "
                        f"the weakest is {worst['name']} at {worst['score']}. ")
            else:
                text = "No perspective has a complete score yet. "
            text += f"{facts['validation_item_count']} items need validation before the report is released."
            return LlmReply(json.dumps({"summary": text}), "end_turn", 0, 0, "stub")
        out = []
        for f in facts:
            weakest = min(f["questions"], key=lambda q: (q["score"], q["id"])) if f["questions"] else None
            r = f"{f['name']} is rated {f['colour']} with a score of {f['score']}."
            if weakest:
                r += f" The weakest question is {weakest['id']} at {weakest['score']}."
            step = " ".join(f["baseline_actions"])
            out.append({"activity": f["activity"], "observation": r,
                        "evidence_used": [e["id"] for e in (weakest["evidence"] if weakest else [])][:2],
                        "next_step": step if len(step) <= 300 else step[:297] + "..."})
        return LlmReply(json.dumps(out), "end_turn", 0, 0, "stub")

    def chat(self, system: str, messages: list, tools: list, max_tokens: int) -> ChatReply:
        task = re.search(r"TASK=(\w+)", system).group(1)
        content, stop = _POLICIES[task]((len(messages) - 1) // 2, messages)
        return ChatReply(content, stop, 0, 0)


# ---- stub agent policy ------------------------------------------------------------------------------
# A fixed script that behaves like a simple agent: look, then act on what it saw. It stands in for a model so the
# loop, the tools, the proposal flow and the audit trail can be run and tested without AWS. A real model chooses
# its own steps; this one always follows the same three-step pattern.
_CONSTRAINT = ("deadline", "before ", "freeze", "cannot", "can't", "budget", "regulat", "complian", "lease", "contract",
               "dependency", "depends on", "only ", "limit", "must stay", "must remain", "blackout")
_PRIORITY = ("priority", "priorit", "goal", "objective", "target", "must ", "need to", "wants to", "aim")


def _calls(messages: list, k: int) -> list[tuple[dict, dict]]:
    """The tool requests of completed turn k, each paired with its result payload ({'json': ...} or {'text': ...})."""
    uses = [b["toolUse"] for b in messages[1 + 2 * k]["content"] if "toolUse" in b]
    by_id = {b["toolResult"]["toolUseId"]: b["toolResult"] for b in messages[2 + 2 * k]["content"] if "toolResult" in b}
    out = []
    for u in uses:
        r = by_id.get(u["toolUseId"], {"status": "error", "content": [{"text": "no result"}]})
        payload = r["content"][0]
        out.append((u, {**payload, "status": r["status"]}))
    return out


def _use(k: int, i: int, name: str, args: dict) -> dict:
    return {"toolUse": {"toolUseId": f"stub-{k}-{i}", "name": name, "input": args}}


def _end(text: str) -> tuple[list, str]:
    return [{"text": text}], "end_turn"


def _stub_evidence(k: int, m: list) -> tuple[list, str]:
    if k == 0:
        return [_use(0, 0, "list_unevidenced_questions", {})], "tool_use"
    qs = _calls(m, 0)[0][1].get("json", {}).get("questions", [])[:12]
    if k == 1:
        if not qs:
            return _end("Every scored question already has evidence.")
        return [_use(1, i, "search_notes", {"query": q["category"], "limit": 2}) for i, q in enumerate(qs)], "tool_use"
    if k == 2:
        uses = []
        for i, (_, res) in enumerate(_calls(m, 1)):
            hits = res.get("json", {}).get("hits", [])
            if hits and len(hits[0]["matched_terms"]) >= 2 and len(hits[0]["passage"]) >= 20:
                args = {"question_id": qs[i]["question_id"], "evidence_id": hits[0]["evidence_id"], "quote": hits[0]["passage"]}
                uses.append(_use(2, i, "propose_evidence_link", args))
        return (uses, "tool_use") if uses else _end("I found no note that clearly supports an unevidenced question.")
    done = sum(r["status"] == "success" for _, r in _calls(m, 2))
    return _end(f"Proposed {done} evidence links; {len(qs) - done} questions had no clearly matching note.")


def _stub_investigate(k: int, m: list) -> tuple[list, str]:
    if k == 0:
        return [_use(0, 0, "list_validation_items", {})], "tool_use"
    data = _calls(m, 0)[0][1].get("json", {})
    items = [i for i in data.get("items", []) if i["kind"] in ("guide_mismatch", "no_evidence", "unverified_high_claim")
             and not i["followup_already_proposed"]][:8]
    if k == 1:
        if not items:
            return _end("There is nothing to follow up.")
        return [_use(1, i, "get_question", {"question_id": it["ref"]}) for i, it in enumerate(items)], "tool_use"
    if k == 2:
        uses = []
        for i, (_, res) in enumerate(_calls(m, 1)):
            it, q = items[i], res.get("json")
            if not q:
                continue
            roles = ["Workshop participants"]
            text = q["question"]
            ask = {"guide_mismatch": f'The rating and the rating description disagree for: "{text}" Which level describes the position today?'}.get(
                it["kind"], f'Please share a document or a contact that shows the position on: "{text}"')
            uses.append(_use(2, i, "propose_followup", {"item_key": it["key"], "audience_roles": roles, "questions": [ask],
                                                        "rationale": it["reason"][:400]}))
        return (uses, "tool_use") if uses else _end("I could not draft any follow-up.")
    done = sum(r["status"] == "success" for _, r in _calls(m, 2))
    return _end(f"Drafted {done} follow-ups.")


def _stub_context(k: int, m: list) -> tuple[list, str]:
    if k == 0:
        return [_use(0, 0, "list_notes", {})], "tool_use"
    notes = _calls(m, 0)[0][1].get("json", {}).get("notes", [])[:10]
    if k == 1:
        if not notes:
            return _end("There are no notes to read.")
        return [_use(1, i, "read_note", {"evidence_id": n["evidence_id"]}) for i, n in enumerate(notes)], "tool_use"
    if k == 2:
        uses, seen = [], set()
        for _, res in _calls(m, 1):
            note = res.get("json")
            if not note:
                continue
            for sent in re.split(r"(?<=[.!?])\s+|\n+", note["note_text"]):
                sent, low = sent.strip(), sent.lower()
                kind = ("constraint" if any(w in low for w in _CONSTRAINT) else
                        "priority" if any(w in low for w in _PRIORITY) else None)
                if kind and 20 <= len(sent) <= 300 and low not in seen and len(uses) < 12:
                    seen.add(low)
                    uses.append(_use(2, len(uses), "propose_context_item",
                                     {"kind": kind, "text": sent if len(sent) >= 10 else sent + " ...",
                                      "evidence_id": note["evidence_id"], "quote": sent}))
        return (uses, "tool_use") if uses else _end("I found no clear priorities or constraints in the notes.")
    done = sum(r["status"] == "success" for _, r in _calls(m, 2))
    return _end(f"Proposed {done} planning-context items.")


def _stub_rating(k: int, m: list) -> tuple[list, str]:
    """A fixed script cannot judge which of five descriptions fits the evidence, so it never proposes a rating."""
    if k == 0:
        return [_use(0, 0, "list_blank_ratings", {})], "tool_use"
    n = _calls(m, 0)[0][1].get("json", {}).get("count", 0)
    return _end(f"{n} blank rating(s). The offline stub does not judge ratings; run with --llm bedrock to get proposals.")


_POLICIES = {"evidence": _stub_evidence, "investigate": _stub_investigate, "context": _stub_context, "rating": _stub_rating}
