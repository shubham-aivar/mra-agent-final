import pytest

from mra.loader import build_response_set
from mra.review import workflow as wf
from tests.conftest import make_raw

SID = "t"


def need_action(svc, sid=SID):
    return [a["id"] for a in svc.result(sid)["activities"] if a["needs_action"]]


def complete_review(svc, sid=SID, actor="tc"):
    """Do everything the gate asks for."""
    res = svc.result(sid)
    for a in res["activities"]:
        if a["needs_action"]:
            svc.decide(sid, a["id"], "approve", actor, owner="Owner A", due_date="2026-12-15")
    for i in res["validation_items"]:
        svc.resolve(sid, wf.item_key(i), "accepted_risk", "reviewed in the workshop", actor)
    svc.set_meta(sid, {"facilitator": "Ingram Micro", "workshop_date": "2026-10-23", "readout_date": "2026-10-30"}, actor)
    svc.confirm_scores(sid, actor)


@pytest.fixture
def run_full(svc, rset, full_ratings):
    svc.run(rset(full_ratings), "tester", {"q.xlsx": "h1"})
    return svc


def test_only_activities_that_need_an_action_can_be_decided(run_full):
    ok = need_action(run_full)[0]
    run_full.decide(SID, ok, "approve", "tc", owner="A", due_date="2026-12-15")
    green = next(a["id"] for a in run_full.result(SID)["activities"] if not a["needs_action"])
    with pytest.raises(ValueError, match="not an activity that needs an action"):
        run_full.decide(SID, green, "approve", "tc")


def test_decision_rules(run_full):
    a = need_action(run_full)[0]
    with pytest.raises(ValueError, match="needs the new text"):
        run_full.decide(SID, a, "edit", "tc")
    with pytest.raises(ValueError, match="needs a comment"):
        run_full.decide(SID, a, "reject", "tc")
    with pytest.raises(ValueError, match="due date"):
        run_full.decide(SID, a, "approve", "tc", due_date="soon")
    run_full.decide(SID, a, "approve", "tc", owner="A")
    run_full.decide(SID, a, "approve", "tc", due_date="2026-12-15")        # a later decision keeps the owner
    assert run_full._state(SID)["decisions"][a]["owner"] == "A"


def test_a_blank_rating_is_not_resolved_with_a_note(svc, rset, golden):
    svc.run(rset(golden["ratings"]), "tester")
    with pytest.raises(ValueError, match="give it a rating"):
        svc.resolve(SID, "blank_rating:Q42", "accepted_risk", "later", "tc")


def test_the_gate_lists_everything_and_blank_ratings_block(svc, rset, golden):
    svc.run(rset(golden["ratings"]), "tester")
    b = svc.status(SID)["blockers"]
    assert any("1 rating(s) are blank (Q42)" in x for x in b)
    assert any("no decision on the action" in x for x in b) and any("not resolved" in x for x in b)
    assert any("not confirmed" in x for x in b) and any("report details missing" in x for x in b)
    with pytest.raises(ValueError, match="blank"):
        svc.confirm_scores(SID, "tc")


def test_finalise_refuses_until_everything_is_done_then_locks(run_full):
    with pytest.raises(wf.ReleaseBlocked):
        run_full.finalise(SID, "tc")
    complete_review(run_full)
    assert run_full.status(SID)["ready_to_finalise"]
    run_full.finalise(SID, "tc")
    for call in (lambda: run_full.decide(SID, need_action(run_full)[0], "approve", "tc"),
                 lambda: run_full.adjust(SID, "Q26", 3, "x", "tc"), lambda: run_full.confirm_scores(SID, "tc"),
                 lambda: run_full.set_meta(SID, {"facilitator": "X"}, "tc")):
        with pytest.raises(wf.ReviewLocked):
            call()
    with pytest.raises(ValueError, match="needs a reason"):
        run_full.reopen(SID, "tc", " ")
    run_full.reopen(SID, "tc", "customer sent a correction")
    assert run_full.status(SID)["blockers"]                      # confirmation was cleared
    assert run_full.verify_audit(SID)["ok"]


def test_a_confirmation_goes_stale_when_a_new_run_changes_the_scores(run_full, rset, full_ratings):
    """F61: the old code kept 'scores confirmed' after a re-run with different data."""
    run_full.confirm_scores(SID, "tc")
    assert run_full.status(SID)["scores_confirmed"] is True
    run_full.run(rset({**full_ratings, "Q26": 1}), "tester")
    st = run_full.status(SID)
    assert st["scores_confirmed"] is False and st["scores_confirmation_stale"] is True
    assert any("changed after they were confirmed" in b for b in st["blockers"])


def test_a_confirmation_survives_a_rerun_that_changes_no_score(run_full, rset, full_ratings):
    run_full.confirm_scores(SID, "tc")
    run_full.run(rset(full_ratings), "tester")
    assert run_full.status(SID)["scores_confirmed"] is True


def test_review_records_that_no_longer_fit_the_data_are_dropped_and_logged(run_full, rset, full_ratings):
    a = next(x for x in run_full.result(SID)["activities"] if x["id"] == "BC-2")
    run_full.decide(SID, "BC-2", "approve", "tc", owner="A", due_date="2026-12-15")
    run_full.resolve(SID, "no_evidence:Q27", "accepted_risk", "ok", "tc")
    assert a["score"] == 3.0
    run_full.run(rset({**full_ratings, "Q27": 5}), "tester")        # BC-2 is now 5.0: no action needed; Q27 now a different item
    s = run_full._state(SID)
    assert "BC-2" not in s["decisions"] and "no_evidence:Q27" not in s["resolutions"]
    dropped = [e for e in run_full.audit_entries(SID) if e["action"] == "reconcile"]
    assert dropped and "decision:BC-2" in dropped[0]["detail"]["dropped"]


def test_input_hashes_survive_a_recompute(run_full):
    """F62: an adjustment used to wipe meta.input_hashes."""
    before = run_full.result(SID)["meta"]["input_hashes"]
    assert before == {"q.xlsx": "h1"}
    run_full.adjust(SID, "Q26", 2, "reviewer view", "tc")
    assert run_full.result(SID)["meta"]["input_hashes"] == before
    runs = [e for e in run_full.audit_entries(SID) if e["action"] == "run"]
    assert all(e["detail"]["input_hashes"] == before for e in runs)


def test_adjusting_a_rating_clears_the_confirmation_and_keeps_the_original(run_full):
    run_full.confirm_scores(SID, "tc")
    run_full.adjust(SID, "Q26", 2, "workshop note", "tc")
    assert run_full.status(SID)["scores_confirmed"] is False
    q = next(x for x in run_full.result(SID)["questions"] if x["question_id"] == "Q26")
    assert (q["score"], q["original_score"]) == (2, 4)


def test_adjust_validates(run_full):
    for q, s, r, msg in (("Q1", 3, "x", "not one of the scored"), ("Q999", 3, "x", "not one of the scored"), ("Q26", 9, "x", "whole number"),
                         ("Q26", True, "x", "whole number"), ("Q26", 3, " ", "written reason")):
        with pytest.raises(ValueError, match=msg):
            run_full.adjust(SID, q, s, r, "tc")


def test_report_details_are_checked(run_full):
    with pytest.raises(ValueError, match="unknown report detail"):
        run_full.set_meta(SID, {"colour": "red"}, "tc")
    with pytest.raises(ValueError, match="workshop_date"):
        run_full.set_meta(SID, {"workshop_date": "yesterday"}, "tc")
    assert run_full.set_meta(SID, {"facilitator": "Ingram Micro"}, "tc")["facilitator"] == "Ingram Micro"


def test_details_given_with_the_input_count(svc, cfg):
    raw = make_raw(cfg, {"Q26": 4}, meta={"facilitator": "F", "workshop_date": "2026-10-23", "readout_date": "2026-10-30"})
    svc.run(build_response_set(raw, cfg), "tester")
    assert svc.status(SID)["details_missing"] == []
