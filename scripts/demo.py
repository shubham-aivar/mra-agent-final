"""A narrated tour of the A04 MRA Agent on SYNTHETIC data: one command, no AWS needed.

    python3 scripts/demo.py                # runs straight through
    python3 scripts/demo.py --step         # pauses after each step
    python3 scripts/demo.py --pptx-template path/to/AWS_MRA_deck.pptx   # also fills the deck

It scores the sample questionnaire (one rating is blank on purpose), shows that release is blocked, lets a person set the
blank rating, runs the agent tasks, plays a scripted reviewer, finalises, and writes the files under demo_runs/.
Each run gets a new folder; nothing is deleted. `--llm bedrock` uses the real model for the text.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mra.adapters.factory import build_llm  # noqa: E402
from mra.adapters.local_storage import LocalStorage  # noqa: E402
from mra.inputs.questionnaire import load_input  # noqa: E402
from mra.loader import load_config  # noqa: E402
from mra.review.workflow import ReleaseBlocked, item_key  # noqa: E402
from mra.service import AssessmentService  # noqa: E402
from mra.settings import load_settings  # noqa: E402

STEP = {"on": False}


def say(title: str, *lines: str) -> None:
    print(f"\n=== {title}")
    for ln in lines:
        print("   " + ln)
    if STEP["on"]:
        input("   (press Enter) ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", action="store_true")
    ap.add_argument("--llm", choices=["stub", "bedrock"], default="stub")
    ap.add_argument("--out", default=str(ROOT / "demo_runs"))
    ap.add_argument("--pptx-template")
    ap.add_argument("--questionnaire", default=str(ROOT / "samples" / "sample_questionnaire.xlsx"))
    ap.add_argument("--notes", default=str(ROOT / "samples" / "notes"))
    a = ap.parse_args(argv)
    STEP["on"] = a.step
    import dataclasses
    run_dir = Path(a.out) / f"run-{datetime.now():%Y%m%d-%H%M%S}"
    s = dataclasses.replace(load_settings(ROOT / "config" / "settings.json", env={}), llm_mode=a.llm, storage_mode="local",
                            local_root=str(run_dir / "store"))
    cfg = load_config(ROOT / "config")
    svc = AssessmentService(LocalStorage(s.local_root), build_llm(s), cfg, s)
    sid = "demo"

    rs, hashes = load_input(a.questionnaire, cfg, a.notes, sid, "Sample Customer",
                            {"facilitator": "Aivar demo", "workshop_date": "2026-10-23", "readout_date": "2026-10-30"})
    r = svc.run(rs, "Demo Analyst", hashes)
    say("STEP 1. The agent scores the questionnaire (code does every calculation)",
        *[f"{p['name']:<11} {'Incomplete' if p['status'] != 'complete' else p['score']}" for p in r["perspectives"]],
        f"Blank ratings: {r['blank_ratings']}. The AWS tool would print 0 for People; this report says Incomplete.")
    say("STEP 2. The release gate refuses, and says why", *svc.status(sid)["blockers"][:4], "...")

    q42 = svc.agent_task(sid, "rating", "Demo Analyst")
    say("STEP 3. The agent looks for a rating for the blank question",
        f"{q42['summary']}", "A model may propose a rating with an exact quote; only a person accepting it changes anything.")
    svc.adjust(sid, "Q42", 1, "No cloud team (the questionnaire's own answer to the team question); level 1", "Demo Reviewer")
    r = svc.result(sid)
    say("STEP 4. A reviewer sets the rating (recorded with a reason, original kept)",
        f"People now {next(p['score'] for p in r['perspectives'] if p['name'] == 'People')}; blank ratings: {r['blank_ratings'] or 'none'}")

    for task in ("evidence", "investigate", "context"):
        out = svc.agent_task(sid, task, "Demo Analyst")
        say(f"STEP 5. Agent task '{task}'", f"stopped: {out['stop_reason']}, {out['tool_calls']} tool calls, proposals: {len(out['proposal_ids'])}")
    props = svc.proposals(sid, status="proposed")
    before = {q["question_id"]: q["score"] for q in svc.result(sid)["questions"]}
    svc.decide_proposals(sid, [(p["id"], "accept", None) for p in props], "Demo Reviewer")
    after = {q["question_id"]: q["score"] for q in svc.result(sid)["questions"]}
    say("STEP 6. The reviewer accepts the proposals", f"{len(props)} accepted. Did any rating change? {'Yes' if before != after else 'No: evidence never changes a rating'}.")

    try:
        svc.confirm_scores(sid, "Demo Reviewer")
        svc.finalise(sid, "Demo Reviewer")
    except ReleaseBlocked as e:
        say("STEP 7a. The gate refuses, as it should", *e.blockers[:3], "...")
    res = svc.result(sid)
    for act in res["activities"]:
        if act["needs_action"]:
            svc.decide(sid, act["id"], "approve", "Demo Reviewer", owner="Customer lead", due_date="2026-12-15")
    for i in res["validation_items"]:
        svc.resolve(sid, item_key(i), "accepted_risk", "reviewed with the customer in the workshop", "Demo Reviewer")
    svc.confirm_scores(sid, "Demo Reviewer")
    f = svc.finalise(sid, "Demo Reviewer")
    say("STEP 7b. Review and release", f"Finalised by {f['by']}.")

    kinds = ["xlsx", "docx", "context"] + (["pptx", "todo"] if a.pptx_template else [])
    templates = {"pptx": Path(a.pptx_template).read_bytes()} if a.pptx_template else {}
    files = svc.export(sid, kinds, "Demo Reviewer", templates)
    out_dir = run_dir / "files"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    k, v = svc.kpis(sid), svc.verify_audit(sid)
    say("STEP 8. The outputs, the KPIs and the audit trail", *[f"Wrote {out_dir / n}" for n in files],
        f"KPIs: evidence {k['evidence_traceability']['at_first_run_pct']}% -> {k['evidence_traceability']['now_pct']}%, "
        f"agent proposals accepted {k['agent_proposals']['acceptance_rate_pct']}%.",
        f"Audit log: {v['entries']} entries, {'chain intact' if v['ok'] else 'PROBLEM'}.",
        "Open the Excel file first (Questions, Actions and Scores are in the AWS layout), then the Word report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
