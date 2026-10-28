"""Command line for local use. Every command calls AssessmentService, the same code the hosted API uses."""
from __future__ import annotations

import argparse
import dataclasses
import getpass
import os
import sys
from pathlib import Path

from .adapters.factory import build_llm, build_storage
from .agent.tasks import TASKS
from .inputs.question_import import import_questions, write_bank
from .inputs.questionnaire import load_input
from .loader import META_FIELDS, InputValidationError, load_config, read_json
from .review.workflow import ReleaseBlocked, ReviewLocked
from .service import AssessmentService
from .settings import load_settings


def _svc(a) -> AssessmentService:
    s = load_settings(a.settings)
    if getattr(a, "llm", None):
        s = dataclasses.replace(s, llm_mode=a.llm)
    return AssessmentService(build_storage(s), build_llm(s), load_config(a.config), s)


def _meta_args(a) -> dict:
    return {k: getattr(a, k) for k in META_FIELDS if getattr(a, k, None)}


def _score(g: dict) -> str:
    return "Incomplete" if g["status"] != "complete" else f"{g['score']:.1f}"


def _print_run(r: dict) -> None:
    m = r["meta"]
    print(f"{m['customer_label']} ({m['set_id']})")
    print("  Perspectives:", "   ".join(f"{p['name']} {_score(p)}" for p in r["perspectives"]))
    print("  Workstreams: ", "   ".join(f"{w['name']} {_score(w)}" for w in r["workstreams"]))
    if r["blank_ratings"]:
        print(f"  BLANK RATINGS: {', '.join(r['blank_ratings'])} (groups that contain them are Incomplete; release is blocked)")
    t, llm = r["traceability"], m["llm"]
    need = sum(a["needs_action"] for a in r["activities"])
    print(f"Evidence: {t['evidenced']}/{t['scored_questions']} rated questions evidenced; unaccounted for: {t['unaccounted_for'] or 'none'}")
    print(f"Activities needing an action: {need}   To validate: {len(r['validation_items'])}   "
          f"Text: {llm['engine']} (calls {llm['calls']}, cached {llm['cache_hits']}, fallbacks {llm['fallbacks']})")
    for w in m.get("input_warnings", []):
        print("  note:", w)
    print(f"Summary [{r['narrative']['source']}]: {r['narrative']['text']}")


def cmd_import_questions(a) -> int:
    mapping = read_json(Path(a.config) / "input_mapping.json")        # not load_config: the bank may not exist yet
    bank, rep = import_questions(a.file, mapping, a.version, a.sheet)
    write_bank(bank, a.out)
    print(f"Imported {rep['questions']} questions in {rep['areas']} categories from {rep['source']} -> {a.out}")
    print("By type:", rep["by_type"], "  Scored ratings:", rep["scored_ratings"])
    print("Scored questions per perspective:", rep["per_perspective"])
    print("Optional modules:", ", ".join(rep["modules"]))
    for w in rep["warnings"]:
        print("  warning:", w)
    print("Next: check config/workstreams.json covers every scored question (python -m mra run will say if not).")
    return 0


def cmd_run(a) -> int:
    svc = _svc(a)
    rs, hashes = load_input(a.questionnaire, svc.cfg, a.evidence, a.set_id, a.customer, _meta_args(a), a.sheet)
    _print_run(svc.run(rs, a.actor, hashes))
    st = svc.cfg_settings
    where = f"s3://{st.bucket}/runs/{rs.set_id}/" if st.storage_mode == "aws" else f"{st.local_root}/runs/{rs.set_id}/"
    print(f"Stored under {where} (status: python -m mra status {rs.set_id})")
    return 0


def cmd_status(a) -> int:
    st = _svc(a).status(a.set_id)
    stale = " (STALE: the scores changed after they were confirmed)" if st["scores_confirmation_stale"] else ""
    print(f"{a.set_id}: {'FINAL' if st['final'] else 'DRAFT'}   blank ratings {st['blank_ratings']}   "
          f"actions decided {st['actions_decided']}/{st['actions_total']}   items resolved {st['items_resolved']}/{st['items_total']}   "
          f"scores confirmed: {st['scores_confirmed']}{stale}")
    for b in st["blockers"]:
        print("  blocker:", b)
    return 0


def cmd_meta(a) -> int:
    meta = _svc(a).set_meta(a.set_id, _meta_args(a), a.actor)
    print("Report details:", {k: meta.get(k, "TBD") for k in META_FIELDS})
    return 0


def cmd_decide(a) -> int:
    _svc(a).decide(a.set_id, a.activity, a.decision, a.actor, a.text, a.owner, a.due, a.comment)
    print("Decision recorded.")
    return 0


def cmd_resolve(a) -> int:
    _svc(a).resolve(a.set_id, a.item, a.resolution, a.note, a.actor)
    print("Resolution recorded.")
    return 0


def cmd_adjust(a) -> int:
    r = _svc(a).adjust(a.set_id, a.question, a.score, a.reason, a.actor)
    print("Rating recorded and the assessment recomputed. Scores must be confirmed again.")
    _print_run(r)
    return 0


def cmd_confirm(a) -> int:
    _svc(a).confirm_scores(a.set_id, a.actor)
    print("Scores confirmed.")
    return 0


def cmd_finalise(a) -> int:
    f = _svc(a).finalise(a.set_id, a.actor)
    print(f"Finalised by {f['by']} at {f['at']}.")
    return 0


def cmd_reopen(a) -> int:
    _svc(a).reopen(a.set_id, a.actor, a.reason)
    print("Reopened.")
    return 0


def cmd_export(a) -> int:
    kinds = [k for k in ("xlsx", "docx", "pptx", "context") if getattr(a, k)] or ["xlsx", "docx"]
    templates, names = {}, {}
    for k in ("docx", "pptx"):
        path = getattr(a, f"{k}_template")
        if path:
            if not Path(path).is_file():
                print(f"The {k} template '{path}' was not found.")
                return 2
            templates[k], names[k] = Path(path).read_bytes(), Path(path).name
    if "pptx" in kinds or a.todo:
        kinds.append("todo")
    svc = _svc(a)
    if "pptx" in templates:
        from .outputs.deck_template import coverage, describe
        try:
            print("Deck template:", names["pptx"], "-", describe(coverage(templates["pptx"], svc.cfg)))
        except ValueError as e:
            print(e)
            return 2
    elif "pptx" in kinds:
        print("Deck template: Ingram MAP deliverable (built in)")
    files = svc.export(a.set_id, kinds, a.actor, templates, names)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
        print("Wrote", out / name)
    return 0


def cmd_check_template(a) -> int:
    from .outputs.deck_template import coverage, default_template_bytes
    f = Path(a.file) if a.file else None
    if f is not None and not f.is_file():
        print(f"'{a.file}' was not found.")
        return 2
    try:
        cov = coverage(f.read_bytes() if f else default_template_bytes(), _svc(a).cfg)
    except ValueError as e:
        print(e)
        return 2
    print(f"Deck: {f.name if f else 'AWS MRA readout format (built in)'}")
    for i in cov["items"]:
        print(f"  {'ok     ' if i['ok'] else 'MISSING'} {i['label']:<32} {i['found']:>3} of {i['expected']}")
    print("Every part can be filled." if cov["complete"] else "Not everything can be filled: " + "; ".join(cov["warnings"]))
    return 0 if cov["complete"] else 3


def cmd_make_template(a) -> int:
    from .outputs.deck_template import make_default_template
    src, out = Path(a.source), Path(a.out)
    if not src.is_file():
        print(f"'{a.source}' was not found.")
        return 2
    if out.exists():
        print(f"'{out}' already exists; choose another --out (nothing is overwritten).")
        return 2
    svc = _svc(a)
    forbid = list(a.forbid or [])
    if a.forbid_questionnaire:
        from .inputs.questionnaire import load_input
        rs, _ = load_input(a.forbid_questionnaire, svc.cfg, None, None, None, {})
        forbid += [ln for ans in rs.answers for ln in str(ans.value).splitlines() if len(ln.strip()) >= 6] + [rs.customer_label]
    try:
        data, report = make_default_template(src.read_bytes(), svc.cfg, forbid)
    except ValueError as e:
        print("Not written:", e)
        return 2
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data) // 1024} KB). Placeholders replace everything the agent fills; people's names in the properties were removed.")
    print(f"Checked every part of the package against {report['checked_removed_lines']} removed lines and "
          f"{report['checked_forbidden']} forbidden strings: none remain.")
    print("Use it with: python3 -m mra export <run> --pptx --pptx-template", out)
    return 0


def cmd_agent(a) -> int:
    out = _svc(a).agent_task(a.set_id, a.task, a.actor)
    print(f"Agent task '{a.task}': stopped because {out['stop_reason']} after {out['steps']} model turns, "
          f"{out['tool_calls']} tool calls, {out['input_tokens'] + out['output_tokens']} tokens.")
    print(f"Proposals created: {out['proposal_ids'] or 'none'}   (review with: python -m mra proposals {a.set_id})")
    if out["summary"]:
        print("Agent summary:", out["summary"])
    return 0


def _describe(p: dict) -> str:
    d = p["payload"]
    if p["kind"] == "evidence_link":
        return f'{d["question_id"]} <- {d["evidence_id"]}: "{d["quote"][:90]}"'
    if p["kind"] == "rating":
        return f'{d["question_id"]} = {d["score"]} ({d["level_text"][:60]}) <- {d["evidence_id"]}: "{d["quote"][:70]}"'
    if p["kind"] == "followup":
        return f'{d["item_key"]} to {", ".join(d["audience_roles"])}: {d["questions"][0][:110]}'
    return f'{d["kind"]}: {d["text"][:90]} ({d["evidence_id"]})'


def cmd_proposals(a) -> int:
    ps = _svc(a).proposals(a.set_id, a.status, a.kind)
    for p in ps:
        print(f"{p['id']}  {p['kind']:<13} {p['status']:<9} {_describe(p)}")
    print(f"{len(ps)} proposals")
    return 0


def cmd_decide_proposals(a) -> int:
    decisions = [(i, "accept", a.comment) for i in a.accept] + [(i, "reject", a.comment) for i in a.reject]
    if not decisions:
        raise ValueError("give at least one id with --accept or --reject")
    out = _svc(a).decide_proposals(a.set_id, decisions, a.actor)
    again = " Scores or evidence changed; confirm the scores again." if out["links_changed"] or out["scores_changed"] else ""
    print(f"Decided {out['decided']} proposals.{again}")
    return 0


def cmd_kpis(a) -> int:
    k = _svc(a).kpis(a.set_id)
    t, e, v, ap = k["turnaround"], k["evidence_traceability"], k["validation_items"], k["agent_proposals"]
    print(f"{k['set_id']} ({k['status']})")
    hours = f"{t['hours']} hours" if t["hours"] is not None else "not finalised yet"
    logged = k["analyst_hours"]["logged"] if k["analyst_hours"] else "none"
    print(f"  Turnaround: {hours} ({t['note']})")
    print(f"  Evidence traceability: {e['at_first_run_pct']}% at first run, {e['now_pct']}% now ({e['scored_questions']} rated questions)")
    print(f"  Validation items: {v['at_first_run']} at first run, {v['now']} now, {v['resolved']} resolved")
    print(f"  Agent proposals acceptance rate: {ap['acceptance_rate_pct']}%   follow-up drafts accepted: {k['follow_up_drafts_accepted']}"
          f"   ratings proposed/accepted: {k['ratings_proposed_accepted'][1]}/{k['ratings_proposed_accepted'][0]}")
    print(f"  Analyst hours logged: {logged}   adjustments: {k['adjustments']}")
    print(f"  Model use: {k['model_use']}")
    print(f"  SOW baseline: {k['sow_baseline']}")
    return 0


def cmd_log_hours(a) -> int:
    _svc(a).log_hours(a.set_id, a.hours, a.actor)
    print("Hours logged.")
    return 0


def cmd_audit(a) -> int:
    svc = _svc(a)
    v = svc.verify_audit(a.set_id)
    verdict = "chain intact" if v["ok"] else f"PROBLEM at entry {v['first_bad_seq']}: {v['problem']}"
    print(f"Audit log: {v['entries']} entries, {verdict}")
    if a.show:
        entries = svc.audit_entries(a.set_id)
        counts: dict[str, int] = {}
        for e in entries:
            counts[e["action"]] = counts.get(e["action"], 0) + 1
        print("Entries by action:", ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
        for e in entries:
            d = e["detail"]
            if e["action"] == "tool_call" and d.get("outcome") != "ok":
                print(f"  #{e['seq']} tool {d.get('tool')} refused: {d.get('error')}")
            elif e["action"] == "llm_call" and d.get("outcome") not in ("parsed",):
                where = f" for {d['activity']}" if d.get("activity") else ""
                print(f"  #{e['seq']} model text {d.get('outcome')}{where}: {'; '.join(d.get('reasons', []))}")
            elif e["action"] == "reconcile":
                print(f"  #{e['seq']} review records dropped because the data changed: {', '.join(d['dropped'])}")
            elif e["action"] == "agent_done":
                tokens = d["input_tokens"] + d["output_tokens"]
                print(f"  #{e['seq']} agent finished ({d['task']}): {d['stop_reason']}, {d['steps']} turns, "
                      f"{d['tool_calls']} tool calls, {tokens} tokens, {len(d['proposals'])} proposals")
    return 0 if v["ok"] else 3


def cmd_purge(a) -> int:
    rec = _svc(a).purge(a.set_id, a.actor, a.confirm)
    print(f"Deleted {rec['objects_removed']} objects for {a.set_id}. A purge record was kept.")
    return 0


def cmd_ui(a) -> int:
    from .ui.server import serve
    templates = {k: v for k, v in (("pptx", a.pptx_template), ("docx", a.docx_template)) if v}
    return serve(_svc(a), a.port, not a.no_browser, templates)


def _add_meta_flags(p) -> None:
    for k in META_FIELDS:
        p.add_argument(f"--{k.replace('_', '-')}", dest=k, help=f"report detail: {k.replace('_', ' ')}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m mra", description=__doc__)
    ap.add_argument("--config", default="config")
    ap.add_argument("--settings", default="config/settings.json")
    ap.add_argument("--as", dest="actor", default=getpass.getuser())
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, sid=True, **kw):
        p = sub.add_parser(name, **kw)
        if sid:
            p.add_argument("set_id")
        p.set_defaults(fn=fn)
        return p

    p = add("ui", cmd_ui, sid=False, help="open the local review page in your browser (this computer only)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--llm", choices=["stub", "bedrock"], help="text engine for the page (default: from config/settings.json)")
    p.add_argument("--pptx-template", default=os.environ.get("MRA_PPTX_TEMPLATE"),
                   help="an AWS-format deck (.pptx) to use instead of the built-in one; remembered by the page (or set MRA_PPTX_TEMPLATE)")
    p.add_argument("--docx-template", default=os.environ.get("MRA_DOCX_TEMPLATE"), help="optional Word template (.docx)")
    p = add("check-template", cmd_check_template, sid=False, help="how much of a deck the agent can fill (default: the built-in deck)")
    p.add_argument("file", nargs="?")
    p = add("make-template", cmd_make_template, sid=False,
            help="make a clean template from a filled-in AWS deck, checking that nothing customer-specific remains")
    p.add_argument("source")
    p.add_argument("--out", required=True)
    p.add_argument("--forbid", action="append", help="text that must not remain anywhere (repeatable), for example the customer name")
    p.add_argument("--forbid-questionnaire", help="that customer's filled-in questionnaire: its name and answers must not remain")
    p = add("import-questions", cmd_import_questions, sid=False, help="turn the AWS MRA questionnaire into the question bank")
    p.add_argument("file")
    p.add_argument("--version", required=True)
    p.add_argument("--sheet")
    p.add_argument("--out", default="config/question_bank.json")
    p = add("run", cmd_run, sid=False, help="score a filled-in questionnaire and draft the observations")
    p.add_argument("questionnaire", help="the filled-in questionnaire (.xlsx, .csv, .tsv) or the canonical .json")
    p.add_argument("--evidence", help="interview notes: a folder of .txt/.md files, a table or a JSON list")
    p.add_argument("--set-id")
    p.add_argument("--customer")
    p.add_argument("--sheet")
    p.add_argument("--llm", choices=["stub", "bedrock"])
    _add_meta_flags(p)
    p = add("meta", cmd_meta, help="set report details (facilitator, dates, contacts)")
    _add_meta_flags(p)
    add("status", cmd_status, help="what is left before the report can be finalised")
    p = add("decide", cmd_decide, help="approve, edit or reject the action for a readiness activity")
    p.add_argument("--activity", required=True, help="activity id, for example OM-1")
    p.add_argument("--decision", required=True, choices=["approve", "edit", "reject"])
    for f in ("text", "owner", "due", "comment"):
        p.add_argument(f"--{f}")
    p = add("resolve", cmd_resolve, help="resolve an item on the validation list")
    p.add_argument("--item", required=True, help="kind:ref, for example no_evidence:Q41")
    p.add_argument("--resolution", required=True, choices=["resolved", "accepted_risk", "not_applicable"])
    p.add_argument("--note", required=True)
    p = add("adjust", cmd_adjust, help="set or change a question's rating, with a written reason")
    p.add_argument("--question", required=True, help="for example Q42")
    p.add_argument("--score", required=True, type=int)
    p.add_argument("--reason", required=True)
    p.add_argument("--llm", choices=["stub", "bedrock"])
    add("confirm", cmd_confirm, help="confirm the scores after review")
    add("finalise", cmd_finalise, help="release gate: finalise once nothing is outstanding")
    p = add("reopen", cmd_reopen, help="reopen a finalised report")
    p.add_argument("--reason", required=True)
    p = add("export", cmd_export, help="write the Excel workbook, Word report and PowerPoint deck")
    for k in ("xlsx", "docx", "pptx", "context", "todo"):
        p.add_argument(f"--{k}", action="store_true")
    p.add_argument("--docx-template", help="a .docx whose styles, header and footer the report should use")
    p.add_argument("--pptx-template", help="your own AWS-format deck (.pptx); default: the built-in one")
    p.add_argument("--out", default="out")
    p = add("agent", cmd_agent, help="let the agent investigate with read-only tools and propose; a person accepts")
    p.add_argument("--task", required=True, choices=list(TASKS))
    p.add_argument("--llm", choices=["stub", "bedrock"])
    p = add("proposals", cmd_proposals, help="list what the agent proposed")
    p.add_argument("--status", choices=["proposed", "accepted", "rejected"])
    p.add_argument("--kind", choices=["evidence_link", "followup", "context_item", "rating"])
    p = add("decide-proposals", cmd_decide_proposals, help="accept or reject agent proposals")
    p.add_argument("--accept", nargs="*", default=[])
    p.add_argument("--reject", nargs="*", default=[])
    p.add_argument("--comment")
    add("kpis", cmd_kpis, help="turnaround, traceability, validation and agent acceptance (SOW KPIs)")
    p = add("log-hours", cmd_log_hours, help="record analyst hours spent (the agent cannot measure these)")
    p.add_argument("--hours", required=True, type=float)
    p = add("audit", cmd_audit, help="check the audit log chain")
    p.add_argument("--show", action="store_true", help="also list refused tool calls, rejected model text and agent runs")
    p = add("purge", cmd_purge, help="permanently delete a run and its derived data")
    p.add_argument("--confirm", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except InputValidationError as e:
        print("Input problems (nothing was changed):")
        for err in e.errors:
            print("  -", err)
        return 2
    except ReleaseBlocked as e:
        print("Cannot finalise yet:")
        for b in e.blockers:
            print("  -", b)
        return 3
    except (ValueError, KeyError, ReviewLocked) as e:
        print("Error:", e.args[0] if e.args else e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
