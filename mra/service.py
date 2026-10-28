"""The operations of the agent. The command line and the hosted API are thin wrappers around this class,
so local and hosted runs behave identically."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from .agent.tasks import TASKS, run_agent_task
from .agent.workspace import Workspace
from .explain.explainer import Explainer, activity_facts
from .loader import build_response_set, canonical, sha256
from .models import ResponseSet
from .outputs import filename, render
from .outputs.ingram_deck import INGRAM_LABEL
from .pipeline import compute_result
from .ports import Llm, Storage
from .report_model import build_report_model
from .review import workflow as wf
from .review.audit import AuditLog
from .settings import Settings
from .traceability import matrix_csv, scores_csv, validation_csv


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def rs_to_raw(rs: ResponseSet) -> dict:
    return {"set_id": rs.set_id, "customer_label": rs.customer_label,
            "ratings": [{"question_id": r.question_id, "score": r.score, "guide_level": r.guide_level, "comment": r.comment}
                        for r in rs.ratings],
            "answers": [{"question_id": a.question_id, "value": a.value} for a in rs.answers],
            "evidence": [{"evidence_id": e.evidence_id, "source": e.source, "kind": e.kind, "text": e.text,
                          "applies_to": list(e.applies_to)} for e in rs.evidence],
            "meta": dict(rs.meta), "warnings": list(rs.warnings)}


def _dump(obj) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")


class AssessmentService:
    def __init__(self, storage: Storage, llm: Llm, cfg: dict, settings: Settings, clock: Callable[[], str] = _now):
        self.s, self.llm, self.cfg, self.cfg_settings, self.clock = storage, llm, cfg, settings, clock

    # ---- storage helpers ---------------------------------------------------
    def _k(self, sid: str, name: str) -> str:
        return f"runs/{sid}/{name}"

    def _audit(self, sid: str) -> AuditLog:
        return AuditLog(self.s, sid, self.clock)

    def _state(self, sid: str) -> dict | None:
        k = self._k(sid, "review.json")
        return json.loads(self.s.get(k)) if self.s.exists(k) else None

    def _save_state(self, sid: str, state: dict) -> None:
        self.s.put(self._k(sid, "review.json"), _dump(state))

    def result(self, sid: str) -> dict:
        k = self._k(sid, "result.json")
        if not self.s.exists(k):
            raise KeyError(f"no run '{sid}'")
        return json.loads(self.s.get(k))

    def _need(self, sid: str) -> tuple[dict, dict]:
        res = self.result(sid)
        return res, self._state(sid) or wf.new_state(sid)

    def _stored_hashes(self, sid: str) -> dict:
        k = self._k(sid, "input/hashes.json")
        return json.loads(self.s.get(k)) if self.s.exists(k) else {}

    # ---- run ---------------------------------------------------------------
    def run(self, rs: ResponseSet, actor: str, input_hashes: dict | None = None, overrides: dict | None = None) -> dict:
        sid, st = rs.set_id, self.cfg_settings
        state = self._state(sid) or wf.new_state(sid)
        if state["final"]:
            raise wf.ReviewLocked("this report is finalised; reopen it (with a reason) before running again")
        if overrides is not None:
            state["overrides"] = overrides
        if input_hashes is None:                       # a recompute keeps the hashes of the files the run was made from
            input_hashes = self._stored_hashes(sid)
        else:
            self.s.put(self._k(sid, "input/hashes.json"), _dump(input_hashes))
        audit = self._audit(sid)
        audit.append(actor, "run", {"input_hashes": input_hashes, "config_hash": self.cfg["config_hash"],
                                    "versions": self.cfg["versions"], "llm": {"name": self.llm.name, "model_id": self.llm.model_id},
                                    "ratings": len(rs.ratings), "answers": len(rs.answers), "evidence": len(rs.evidence),
                                    "adjusted_questions": len(state["overrides"])})
        evidence_text = {e.evidence_id: e.text for e in rs.evidence}
        links = [p["payload"] for p in self._proposals(sid) if p["kind"] == "evidence_link" and p["status"] == "accepted"
                 and p["payload"]["evidence_id"] in evidence_text]
        result = compute_result(rs, self.cfg, state["overrides"], links)
        dropped = wf.reconcile(state, result)
        if dropped:
            audit.append(actor, "reconcile", {"dropped": dropped})

        comments: dict[str, list[str]] = {}
        for r in rs.ratings:
            if r.comment.strip():
                comments.setdefault(r.question_id, []).append(r.comment.strip())
        qby = {q["question_id"]: q for q in result["questions"]}
        items = [activity_facts(a, qby, evidence_text, self.cfg["rubric"], st.mask_emails_in_prompts, comments)
                 for a in result["activities"] if a["needs_action"]]
        all_ids = set(self.cfg["qmap"]) | {a["id"] for a in self.cfg["areas"]} | {a["id"] for a in self.cfg["activities"]}

        def on_call(ev: dict) -> None:
            blob = {k: ev.pop(k) for k in ("system", "user", "reply") if k in ev}
            if blob:                                    # a rejection of one item has no prompt of its own
                ev["artifact"] = sha256(canonical(blob))
                self.s.put(self._k(sid, f"audit_artifacts/{ev['artifact']}.json"), _dump(blob))
            audit.append("agent", "llm_call", ev)

        ex = Explainer(self.llm, self.s, self.cfg["prompts"], st.batch_size, st.max_retries, st.max_tokens,
                       all_ids, set(evidence_text), on_call)
        result["explanations"] = ex.explain_activities(items)
        done = [p for p in result["perspectives"] if p["score"] is not None]
        if not done:
            result["narrative"] = {"text": "There is not enough data to give a readiness summary.", "source": "code"}
        else:
            result["narrative"] = ex.explain_summary({
                "scale": [self.cfg["rubric"]["scale_min"], self.cfg["rubric"]["scale_max"]],
                "perspectives": [{"name": p["name"], "score": p["score"]} for p in result["perspectives"]],
                "workstreams": [{"name": w["name"], "score": w["score"]} for w in result["workstreams"]],
                "incomplete": result["focus"]["incomplete"], "validation_item_count": len(result["validation_items"])})
        result["meta"].update({"generated_at": self.clock(), "input_hashes": input_hashes,
                               "llm": {"engine": self.llm.name, "model_id": self.llm.model_id, **ex.stats}})

        self.s.put(self._k(sid, "input/response_set.json"), _dump(rs_to_raw(rs)))
        self.s.put(self._k(sid, "result.json"), _dump(result))
        groups = {k: result[k] for k in ("perspectives", "workstreams", "activities", "categories")}
        self.s.put(self._k(sid, "scores.csv"), scores_csv(groups).encode())
        self.s.put(self._k(sid, "traceability_matrix.csv"), matrix_csv(result["questions"]).encode())
        self.s.put(self._k(sid, "validation_items.csv"), validation_csv(result["validation_items"]).encode())
        self.s.put(self._k(sid, "cache_keys.json"), _dump(sorted(ex.touched)))
        self._save_state(sid, state)
        audit.append("agent", "result", {"result_hash": sha256(canonical(result)), "fingerprint": result["meta"]["scores_fingerprint"],
                                         "activities_needing_action": len(items), "blank_ratings": len(result["blank_ratings"]),
                                         "validation_items": len(result["validation_items"]), "llm_stats": ex.stats,
                                         "scored_questions": result["traceability"]["scored_questions"],
                                         "evidenced_pct": result["traceability"]["evidenced_pct"]})
        return result

    # ---- review ------------------------------------------------------------
    def decide(self, sid: str, activity: str, decision: str, actor: str, text=None, owner=None, due_date=None, comment=None):
        res, state = self._need(sid)
        wf.set_decision(state, res, activity, decision, actor, self.clock(), text, owner, due_date, comment)
        self._save_state(sid, state)
        self._audit(sid).append(actor, "decision", {"activity": activity, **state["decisions"][activity]})

    def resolve(self, sid: str, key: str, resolution: str, note: str, actor: str):
        res, state = self._need(sid)
        wf.set_resolution(state, res, key, resolution, note, actor, self.clock())
        self._save_state(sid, state)
        self._audit(sid).append(actor, "resolution", {"item": key, **state["resolutions"][key]})

    def set_meta(self, sid: str, values: dict, actor: str) -> dict:
        res, state = self._need(sid)
        meta = wf.set_meta(state, values, actor, self.clock())
        self._save_state(sid, state)
        self._audit(sid).append(actor, "meta", {"values": {k: v for k, v in values.items() if v}})
        return {**res["meta"].get("report", {}), **meta}

    def confirm_scores(self, sid: str, actor: str):
        res, state = self._need(sid)
        wf.ensure_unlocked(state)
        if res["blank_ratings"]:
            raise ValueError(f"{len(res['blank_ratings'])} rating(s) are blank ({', '.join(res['blank_ratings'][:6])}); "
                             "give them a rating before confirming the scores")
        state["scores_confirmed"] = {"by": actor, "at": self.clock(), "fingerprint": res["meta"]["scores_fingerprint"]}
        self._save_state(sid, state)
        self._audit(sid).append(actor, "confirm_scores", state["scores_confirmed"])

    def record_adjustment(self, sid: str, question_id: str, score: int, reason: str, actor: str, proposal: str | None = None) -> None:
        """Validate and store a reviewer's rating for a question (blank or not). The scores are recomputed by recompute()."""
        res, state = self._need(sid)
        wf.ensure_unlocked(state)
        rub = self.cfg["rubric"]
        q = self.cfg["qmap"].get(question_id)
        if q is None or q.type != "rating" or q.perspective is None:
            raise ValueError(f"'{question_id}' is not one of the scored rating questions")
        if isinstance(score, bool) or not isinstance(score, int) or not rub["scale_min"] <= score <= rub["scale_max"]:
            raise ValueError(f"the rating must be a whole number from {rub['scale_min']} to {rub['scale_max']}")
        if not (reason or "").strip():
            raise ValueError("an adjustment needs a written reason")
        before = next(x for x in res["questions"] if x["question_id"] == question_id)["score"]
        state["overrides"][question_id] = {"score": score, "reason": reason.strip(), "by": actor, "at": self.clock(),
                                           **({"proposal": proposal} if proposal else {})}
        state["scores_confirmed"] = None          # a changed rating must be confirmed again
        self._save_state(sid, state)
        self._audit(sid).append(actor, "adjustment", {"question": question_id, "from": before, "to": score, "reason": reason.strip()})

    def recompute(self, sid: str, actor: str) -> dict:
        """Run again on the stored answers, using the stored adjustments. Cached text is reused; input hashes are kept."""
        raw = json.loads(self.s.get(self._k(sid, "input/response_set.json")))
        return self.run(build_response_set(raw, self.cfg), actor)

    def adjust(self, sid: str, question_id: str, score: int, reason: str, actor: str) -> dict:
        self.record_adjustment(sid, question_id, score, reason, actor)
        return self.recompute(sid, actor)

    def status(self, sid: str) -> dict:
        res, state = self._need(sid)
        blockers = wf.gate(res, state)
        conf = state["scores_confirmed"]
        needing = [a for a in res["activities"] if a["needs_action"]]
        meta = wf.effective_meta(res, state)
        return {"set_id": sid, "final": state["final"], "ready_to_finalise": not blockers, "blockers": blockers,
                "actions_decided": sum(a["id"] in state["decisions"] for a in needing), "actions_total": len(needing),
                "items_resolved": sum(wf.item_key(i) in state["resolutions"] for i in res["validation_items"]
                                      if i["kind"] != "blank_rating"),
                "items_total": sum(i["kind"] != "blank_rating" for i in res["validation_items"]),
                "blank_ratings": len(res["blank_ratings"]),
                "scores_confirmed": bool(conf and conf.get("fingerprint") == res["meta"]["scores_fingerprint"]),
                "scores_confirmation_stale": bool(conf and conf.get("fingerprint") != res["meta"]["scores_fingerprint"]),
                "details_missing": [k for k in wf.REQUIRED_META if not meta.get(k)],
                "adjusted_questions": sorted(state["overrides"])}

    def finalise(self, sid: str, actor: str) -> dict:
        res, state = self._need(sid)
        wf.ensure_unlocked(state)
        blockers = wf.gate(res, state)
        if blockers:
            raise wf.ReleaseBlocked(blockers)
        entry = self._audit(sid).append(actor, "finalise", {"result_hash": sha256(canonical(res)), "review_hash": sha256(canonical(state)),
                                                            "fingerprint": res["meta"]["scores_fingerprint"]})
        state["final"] = {"by": actor, "at": self.clock(), "fingerprint": res["meta"]["scores_fingerprint"],
                          "audit_head": {"seq": entry["seq"], "hash": entry["hash"]}}
        self._save_state(sid, state)
        return state["final"]

    def reopen(self, sid: str, actor: str, reason: str) -> None:
        _, state = self._need(sid)
        if not state["final"]:
            raise ValueError("the report is not finalised")
        if not (reason or "").strip():
            raise ValueError("reopening needs a reason")
        state["final"], state["scores_confirmed"] = None, None
        self._save_state(sid, state)
        self._audit(sid).append(actor, "reopen", {"reason": reason.strip()})

    # ---- outputs, audit, deletion -----------------------------------------
    def export(self, sid: str, kinds: list[str], actor: str, templates: dict[str, bytes] | None = None,
               template_names: dict[str, str] | None = None) -> dict[str, bytes]:
        """Write the files. A deck with no template uses the built-in default; a custom deck is checked against the default and every
        part it cannot fill is listed in the open-items file. The audit log records which template each file was made from."""
        res, state = self._need(sid)
        model = build_report_model(res, state, self.clock(), self._proposals(sid), self.cfg)
        custom = (templates or {}).get("pptx")
        model["template_name"] = (template_names or {}).get("pptx") if custom is not None else INGRAM_LABEL
        if custom is not None:
            from .outputs.deck_template import coverage
            model["template_warnings"] = coverage(custom, self.cfg)["warnings"]
        out: dict[str, bytes] = {}
        for kind in kinds:
            tpl = (templates or {}).get(kind)
            data = render(kind, model, tpl)
            name = filename(sid, model["status"], kind)
            self.s.put(self._k(sid, f"exports/{name}"), data)
            detail = {"file": name, "sha256": sha256(data), "status": model["status"]}
            if kind in ("pptx", "docx"):
                from .outputs.deck_template import template_hash
                detail["template"] = {"name": (template_names or {}).get(kind) if tpl is not None else INGRAM_LABEL if kind == "pptx" else None,
                                      "sha256": template_hash(tpl) if kind == "pptx" else (sha256(tpl) if tpl else None)}
            self._audit(sid).append(actor, "export", detail)
            out[name] = data
        return out

    def audit_entries(self, sid: str) -> list[dict]:
        return self._audit(sid).entries()

    def verify_audit(self, sid: str) -> dict:
        state = self._state(sid)
        anchor = state["final"]["audit_head"] if state and state["final"] else None
        return self._audit(sid).verify(anchor)

    def purge(self, sid: str, actor: str, confirm: bool) -> dict:
        """Delete every stored object for a run, including cached model text derived from it. Needs confirm=True."""
        if not confirm:
            raise ValueError("purge deletes data permanently; pass confirm=True to proceed")
        state = self._state(sid)
        ck = self._k(sid, "cache_keys.json")
        cache_keys = json.loads(self.s.get(ck)) if self.s.exists(ck) else []
        head = state["final"]["audit_head"] if state and state["final"] else None
        removed = self.s.delete_prefix(f"runs/{sid}/")
        for k in cache_keys:
            if self.s.exists(k):
                self.s.delete_prefix(k)
                removed += 1
        record = {"set_id": sid, "purged_by": actor, "at": self.clock(), "objects_removed": removed, "final_audit_head": head}
        self.s.put(f"purge_log/{sid}-{self.clock().replace(':', '')}.json", _dump(record))
        return record

    # ---- agent tasks and proposals ----------------------------------------------------------
    def _proposals(self, sid: str) -> list[dict]:
        k = self._k(sid, "proposals.json")
        return json.loads(self.s.get(k)) if self.s.exists(k) else []

    def _save_proposals(self, sid: str, props: list[dict]) -> None:
        self.s.put(self._k(sid, "proposals.json"), _dump(props))

    def agent_task(self, sid: str, task: str, actor: str) -> dict:
        """Let the model investigate with read-only tools and store what it proposes. It changes nothing else."""
        if task not in TASKS:
            raise ValueError(f"unknown agent task '{task}' (available: {list(TASKS)})")
        res, state = self._need(sid)
        wf.ensure_unlocked(state)
        rs = build_response_set(json.loads(self.s.get(self._k(sid, "input/response_set.json"))), self.cfg)
        st, props, audit = self.cfg_settings, self._proposals(sid), self._audit(sid)
        ws = Workspace(res, rs, self.cfg, props, st.mask_emails_in_prompts, self.clock, task)
        limits = {"max_steps": st.agent_max_steps, "max_tool_calls": st.agent_max_tool_calls, "max_total_tokens": st.agent_max_tokens}
        audit.append(actor, "agent_task", {"task": task, "llm": {"name": self.llm.name, "model_id": self.llm.model_id}, "limits": limits})
        out = run_agent_task(task, ws, self.llm, self.cfg["prompts"], max_steps=st.agent_max_steps,
                             max_tool_calls=st.agent_max_tool_calls, max_total_tokens=st.agent_max_tokens,
                             max_output_tokens=st.max_tokens, on_event=lambda ev: audit.append("agent", "tool_call", ev))
        transcript = out.pop("messages")
        h = sha256(canonical(transcript))
        self.s.put(self._k(sid, f"audit_artifacts/{h}.json"), _dump(transcript))
        self._save_proposals(sid, props + ws.new)
        ids = [p["id"] for p in ws.new]
        audit.append("agent", "agent_done", {**{k: v for k, v in out.items() if k != "summary"}, "proposals": ids, "transcript": h})
        return {**out, "proposal_ids": ids}

    def proposals(self, sid: str, status: str | None = None, kind: str | None = None) -> list[dict]:
        self.result(sid)
        return [p for p in self._proposals(sid) if (status is None or p["status"] == status) and (kind is None or p["kind"] == kind)]

    def decide_proposals(self, sid: str, decisions: list[tuple[str, str, str | None]], actor: str, recompute: bool = True) -> dict:
        """decisions: (proposal id, 'accept' or 'reject', comment). Accepting an evidence link changes traceability only.
        Accepting a rating proposal records a reviewer adjustment (a person set that rating); the model never does."""
        res, state = self._need(sid)
        wf.ensure_unlocked(state)
        props = self._proposals(sid)
        by_id = {p["id"]: p for p in props}
        changed, n, scores_changed = False, 0, False
        for pid, decision, comment in decisions:
            if pid not in by_id:
                hint = ("; there are no proposals yet, run an agent task first (python -m mra agent <run> --task evidence)"
                        if not props else "; list them with: python -m mra proposals <run>")
                raise ValueError(f"no proposal '{pid}'{hint}")
            if decision not in ("accept", "reject"):
                raise ValueError("decision must be 'accept' or 'reject'")
            p, new = by_id[pid], "accepted" if decision == "accept" else "rejected"
            if p["status"] == new:
                continue
            if p["kind"] == "rating":
                qid, now_score = p["payload"]["question_id"], next(q for q in res["questions"] if q["question_id"] == p["payload"]["question_id"])
                if new == "accepted":
                    if now_score["status"] != "unanswered":
                        raise ValueError(f"{qid} already has a rating; proposal {pid} no longer applies")
                    state["overrides"][qid] = {"score": p["payload"]["score"], "by": actor, "at": self.clock(), "proposal": pid,
                                               "reason": f"accepted agent proposal {pid}: {p['payload']['rationale']}"}
                    scores_changed = True
                elif state["overrides"].get(qid, {}).get("proposal") == pid:
                    del state["overrides"][qid]
                    scores_changed = True
            changed |= p["kind"] == "evidence_link" and "accepted" in (p["status"], new)
            p["status"], p["decision"] = new, {"by": actor, "at": self.clock(), "comment": (comment or "").strip() or None}
            self._audit(sid).append(actor, "proposal_decision", {"id": pid, "kind": p["kind"], "status": new,
                                                                 "comment": p["decision"]["comment"]})
            n += 1
        self._save_proposals(sid, props)
        if changed or scores_changed:
            state["scores_confirmed"] = None          # the picture changed: confirm again
            self._save_state(sid, state)
            if recompute:
                self.recompute(sid, actor)
        return {"decided": n, "links_changed": changed, "scores_changed": scores_changed}

    # ---- KPIs (SOW: turnaround, effort, follow-ups, traceability) --------------------------------
    def log_hours(self, sid: str, hours: float, actor: str) -> None:
        _, state = self._need(sid)
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours <= 200:
            raise ValueError("hours must be a number above 0 and at most 200")
        state.setdefault("manual_kpis", {}).setdefault("analyst_hours", []).append(
            {"hours": float(hours), "by": actor, "at": self.clock()})
        self._save_state(sid, state)
        self._audit(sid).append(actor, "kpi_hours", {"hours": float(hours)})

    def kpis(self, sid: str) -> dict:
        res, state = self._need(sid)
        entries = self._audit(sid).entries()
        runs = [e for e in entries if e["action"] == "run"]
        results = [e for e in entries if e["action"] == "result"]
        fin = next((e for e in reversed(entries) if e["action"] == "finalise"), None)
        hours = None
        if runs and fin:
            hours = round((datetime.fromisoformat(fin["ts"]) - datetime.fromisoformat(runs[0]["ts"])).total_seconds() / 3600, 2)
        props = self._proposals(sid)
        kinds = {k: {s: sum(p["kind"] == k and p["status"] == s for p in props) for s in ("proposed", "accepted", "rejected")}
                 for k in ("evidence_link", "followup", "context_item", "rating")}
        decided = sum(v["accepted"] + v["rejected"] for v in kinds.values())
        tokens = [e["detail"] for e in entries if e["action"] in ("llm_call", "agent_done")]
        logged = [h["hours"] for h in state.get("manual_kpis", {}).get("analyst_hours", [])]
        return {
            "set_id": sid, "status": "FINAL" if state["final"] else "DRAFT",
            "turnaround": {"first_run_at": runs[0]["ts"] if runs else None, "finalised_at": fin["ts"] if fin else None,
                           "hours": hours, "note": "measured from the first run, not from when the responses arrived"},
            "evidence_traceability": {"at_first_run_pct": results[0]["detail"].get("evidenced_pct") if results else None,
                                      "now_pct": res["traceability"]["evidenced_pct"],
                                      "scored_questions": res["traceability"]["scored_questions"]},
            "validation_items": {"at_first_run": results[0]["detail"]["validation_items"] if results else None,
                                 "now": len(res["validation_items"]),
                                 "resolved": sum(wf.item_key(i) in state["resolutions"] for i in res["validation_items"])},
            "agent_proposals": {**kinds, "acceptance_rate_pct": (
                round(100 * sum(v["accepted"] for v in kinds.values()) / decided, 1) if decided else None)},
            "follow_up_drafts_accepted": kinds["followup"]["accepted"],
            "ratings_proposed_accepted": [kinds["rating"]["accepted"], sum(kinds["rating"].values())],
            "adjustments": len(state["overrides"]),
            "model_use": {"agent_runs": sum(e["action"] == "agent_done" for e in entries),
                          "input_tokens": sum(d.get("input_tokens", 0) for d in tokens),
                          "output_tokens": sum(d.get("output_tokens", 0) for d in tokens)},
            "analyst_hours": {"logged": round(sum(logged), 2), "entries": len(logged)} if logged else None,
            "sow_baseline": "about 16 analyst hours and about 1 week elapsed; target: scored within 1 business day",
        }
