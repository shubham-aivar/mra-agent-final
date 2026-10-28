"""Ask the model to describe each readiness activity that needs an action, check every answer, retry, then fall back.

Cost and safety rules:
- Only activities that need an action, and one overall summary, go to the model. The rest keep the questionnaire's own wording.
- Activities go in small batches. Each is checked on its own, so one bad item never spoils the batch.
- Only checked answers are cached (keyed by facts, model and prompt version), so a re-run costs nothing.
- Nothing a model returns can change a score. A rejected item gets labelled fallback text after the retries.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from ..loader import canonical, sha256
from ..ports import Llm, Storage
from .checker import check_activity_output, check_summary_output, id_regex

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CACHE = "cache/explain/"


def _strip_fences(t: str) -> str:
    t = t.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, flags=re.S)
    return m.group(1) if m else t


def fill(template: str, limits: dict) -> str:
    """Substitute {name} placeholders. Plain replace, because the templates contain JSON braces."""
    for k, v in limits.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def mask(text: str, on: bool) -> str:
    return EMAIL.sub("[email]", text) if on else text


def activity_facts(act: dict, qby: dict[str, dict], evidence: dict[str, str], rubric: dict, mask_emails: bool,
                   comments: dict[str, list[str]]) -> dict:
    qs = []
    for qid in act["question_ids"]:
        q = qby[qid]
        if q["status"] != "scored":
            continue
        qs.append({"id": qid, "score": q["score"], "level_text": q["level_text"], "next_level_text": q["next_level_text"],
                   "comments": [mask(c, mask_emails)[:300] for c in comments.get(qid, [])[:3]],
                   "evidence": [{"id": e, "text": mask(evidence[e], mask_emails)[:500]} for e in q["evidence_ids"][:3] if e in evidence]})
    return {"activity": act["id"], "name": act["name"], "workstream": act["workstream"], "phase": act["phase"],
            "score": act["score"], "colour": act["colour"], "scale": [rubric["scale_min"], rubric["scale_max"]],
            "questions": qs, "baseline_actions": [a["text"] for a in act["actions"]]}


class Explainer:
    def __init__(self, llm: Llm, storage: Storage | None, prompts: dict, batch_size: int, max_retries: int,
                 max_tokens: int, all_ids: set[str], evidence_ids: set[str],
                 on_call: Callable[[dict], None] | None = None):
        self.llm, self.storage, self.p = llm, storage, prompts
        self.batch_size, self.max_retries, self.max_tokens = batch_size, max_retries, max_tokens
        self.rx = id_regex(all_ids | evidence_ids)
        self.forbidden = [re.compile(x, re.I) for x in prompts["forbidden_patterns"]]
        self.on_call = on_call or (lambda e: None)
        self.prompts_hash = sha256(canonical(prompts))[:12]
        self.stats = {"calls": 0, "cache_hits": 0, "fallbacks": 0, "rejected_items": 0, "errors": 0,
                      "input_tokens": 0, "output_tokens": 0}
        self._truncated = False
        self.touched: set[str] = set()
        self.source = "stub" if llm.name == "stub" else "model"

    # ---- cache -------------------------------------------------------------
    def _key(self, facts: dict) -> str:
        return CACHE + sha256(canonical({"m": self.llm.model_id, "p": self.prompts_hash, "f": facts}))[:32] + ".json"

    def _cache_get(self, facts: dict) -> dict | None:
        if self.storage is None:
            return None
        k = self._key(facts)
        if not self.storage.exists(k):
            return None
        self.touched.add(k)
        return json.loads(self.storage.get(k))

    def _cache_put(self, facts: dict, value: dict) -> None:
        if self.storage is not None:
            k = self._key(facts)
            self.touched.add(k)
            self.storage.put(k, json.dumps(value, sort_keys=True).encode())

    # ---- one model call ----------------------------------------------------
    def _call(self, user: str, purpose: str) -> tuple[object | None, str | None]:
        """Return (parsed_json, problem). Never raises."""
        self.stats["calls"] += 1
        try:
            reply = self.llm.complete(self.p["system"], user, self.max_tokens)
        except Exception as e:  # network, throttling, access: counted, retried, then fallback
            self.stats["errors"] += 1
            self.on_call({"purpose": purpose, "system": self.p["system"], "user": user, "reply": "", "outcome": "error",
                          "reasons": [f"{type(e).__name__}: {str(e)[:200]}"], "model_id": self.llm.model_id})
            return None, f"model call failed ({type(e).__name__})"
        self.stats["input_tokens"] += reply.input_tokens
        self.stats["output_tokens"] += reply.output_tokens
        event = {"purpose": purpose, "system": self.p["system"], "user": user, "reply": reply.text,
                 "model_id": reply.model_id or self.llm.model_id, "input_tokens": reply.input_tokens,
                 "output_tokens": reply.output_tokens, "stop_reason": reply.stop_reason}
        if reply.stop_reason == "max_tokens":
            self._truncated = True
            self.on_call({**event, "outcome": "rejected", "reasons": ["reply was cut off at the token limit"]})
            return None, "reply was cut off at the token limit"
        try:
            parsed = json.loads(_strip_fences(reply.text))
        except json.JSONDecodeError:
            self.on_call({**event, "outcome": "rejected", "reasons": ["reply is not valid JSON"]})
            return None, "reply is not valid JSON"
        event["outcome"] = "parsed"
        self.on_call(event)
        return parsed, None

    # ---- activities --------------------------------------------------------
    def _activity_prompt(self, batch: list[dict], reasons: dict[str, list[str]]) -> str:
        lim = self.p["limits"]
        task = fill(self.p["activity_task"], lim)
        note = ""
        bad = {k: v for k, v in reasons.items() if any(f["activity"] == k for f in batch)}
        if bad:
            note = "\n" + self.p["retry_note"] + "\n" + "\n".join(f"- {a}: {'; '.join(r)}" for a, r in sorted(bad.items()))
        facts = json.dumps(batch, sort_keys=True, ensure_ascii=False)
        return f"{task}{note}\n<facts>{facts}</facts>"

    def explain_activities(self, items: list[dict]) -> dict[str, dict]:
        results: dict[str, dict] = {}
        pending: list[dict] = []
        for f in items:
            hit = self._cache_get(f)
            if hit:
                self.stats["cache_hits"] += 1
                results[f["activity"]] = hit
            else:
                pending.append(f)
        reasons: dict[str, list[str]] = {}
        attempt = 0
        while pending and attempt <= self.max_retries:
            size = 1 if self._truncated else self.batch_size
            still: list[dict] = []
            for i in range(0, len(pending), size):
                batch = pending[i:i + size]
                parsed, problem = self._call(self._activity_prompt(batch, reasons), "activities")
                if problem or not isinstance(parsed, list):
                    for f in batch:
                        reasons[f["activity"]] = [problem or "reply is not a JSON list"]
                    still += batch
                    continue
                by_id = {o.get("activity"): o for o in parsed if isinstance(o, dict)}
                for f in batch:
                    out = by_id.get(f["activity"])
                    why = ["no item was returned for this activity"] if out is None else check_activity_output(
                        f, out, self.p["limits"], self.forbidden, self.rx)
                    if why:
                        self.stats["rejected_items"] += 1
                        reasons[f["activity"]] = why
                        self.on_call({"purpose": "activities", "outcome": "item_rejected", "activity": f["activity"], "reasons": why,
                                      "model_id": self.llm.model_id})
                        still.append(f)
                        continue
                    value = {"observation": {"text": out["observation"].strip(), "source": self.source},
                             "next_step": {"text": out["next_step"].strip(), "source": self.source},
                             "evidence_used": sorted(set(out.get("evidence_used", []))), "attempts": attempt + 1}
                    self._cache_put(f, value)
                    results[f["activity"]] = value
            pending, attempt = still, attempt + 1
        for f in pending:
            self.stats["fallbacks"] += 1
            results[f["activity"]] = {
                "observation": {"text": (f"FALLBACK TEXT: {f['name']} is rated {f['colour']} (score {f['score']}). "
                                         "The model text did not pass the checks; the questionnaire's own wording applies."),
                                "source": "fallback"},
                "next_step": {"text": " ".join(f["baseline_actions"]), "source": "library"},
                "evidence_used": [], "attempts": attempt, "reasons": reasons.get(f["activity"], [])}
        return results

    # ---- overall summary ---------------------------------------------------
    def explain_summary(self, facts: dict) -> dict:
        hit = self._cache_get(facts)
        if hit:
            self.stats["cache_hits"] += 1
            return hit
        task = fill(self.p["summary_task"], self.p["limits"])
        why: list[str] = []
        for _ in range(self.max_retries + 1):
            note = ("\n" + self.p["retry_note"] + "\n- " + "; ".join(why)) if why else ""
            parsed, problem = self._call(f"{task}{note}\n<facts>{json.dumps(facts, sort_keys=True)}</facts>", "summary")
            why = [problem] if problem else check_summary_output(facts, parsed, self.p["limits"], self.forbidden, self.rx)
            if why and not problem:
                self.on_call({"purpose": "summary", "outcome": "item_rejected", "reasons": why, "model_id": self.llm.model_id})
            if not why:
                value = {"text": parsed["summary"].strip(), "source": self.source}
                self._cache_put(facts, value)
                return value
        self.stats["fallbacks"] += 1
        parts = ", ".join(f"{p['name']} {p['score']}" for p in facts["perspectives"] if p["score"] is not None)
        return {"text": f"FALLBACK TEXT: perspective scores (1 to 5): {parts}.", "source": "fallback"}
