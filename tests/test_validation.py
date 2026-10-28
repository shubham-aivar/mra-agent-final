import pytest

from mra.pipeline import compute_result, scores_fingerprint


def kinds(res):
    return [(i["kind"], i["ref"]) for i in res["validation_items"]]


def test_a_blank_rating_comes_first_and_the_rest_follow_in_a_fixed_order(cfg, golden, rset):
    res = compute_result(rset(golden["ratings"]), cfg)
    k = kinds(res)
    assert k[0] == ("blank_rating", "Q42")
    order = [x[0] for x in k]
    assert order == sorted(order, key=["blank_rating", "guide_mismatch", "unverified_high_claim", "no_evidence"].index)
    assert [i["item_id"] for i in res["validation_items"]][:3] == ["V001", "V002", "V003"]


def test_high_ratings_without_evidence_are_called_unverified(cfg, rset):
    res = compute_result(rset({"Q26": 4, "Q27": 3}), cfg)
    assert ("unverified_high_claim", "Q26") in kinds(res) and ("no_evidence", "Q27") in kinds(res)


@pytest.mark.parametrize("target", ["Q26", "BUS-04", "BC-1"])
def test_evidence_tagged_to_a_question_category_or_activity_clears_the_item(cfg, rset, target):
    ev = [{"evidence_id": "E1", "text": "CIO approved", "applies_to": [target]}]
    res = compute_result(rset({"Q26": 4}, evidence=ev), cfg)
    q = next(x for x in res["questions"] if x["question_id"] == "Q26")
    assert q["traceable"] and q["evidence_status"] == "evidenced" and "E1" in q["evidence_ids"]
    assert ("unverified_high_claim", "Q26") not in kinds(res)


def test_every_rated_question_is_evidenced_or_listed(cfg, golden, rset):
    res = compute_result(rset(golden["ratings"], evidence=[{"evidence_id": "E1", "text": "t", "applies_to": ["BUS-01", "Q41"]}]), cfg)
    t = res["traceability"]
    assert t["unaccounted_for"] == [] and t["scored_questions"] == 61
    assert t["evidenced"] + sum(i["kind"] in ("no_evidence", "unverified_high_claim") for i in res["validation_items"]) == 61


def test_the_true_share_is_reported_never_assumed(cfg, rset):
    assert compute_result(rset({"Q26": 4, "Q27": 3}), cfg)["traceability"]["evidenced_pct"] == 0.0


def test_a_disagreement_between_rating_and_guide_text_is_an_item_until_it_is_overridden(cfg, rset):
    raw = {"set_id": "g", "customer_label": "G", "ratings": [{"question_id": "Q26", "score": 2, "guide_level": 4}], "answers": [], "evidence": []}
    from mra.loader import build_response_set
    rs = build_response_set(raw, cfg)
    assert ("guide_mismatch", "Q26") in kinds(compute_result(rs, cfg))
    fixed = compute_result(rs, cfg, {"Q26": {"score": 4, "reason": "checked", "by": "tc", "at": "t"}})
    assert ("guide_mismatch", "Q26") not in kinds(fixed)


def test_optional_modules_follow_their_gate_answers(cfg, golden, rset):
    res = compute_result(rset(golden["ratings"], answers={"Q7": "Yes", "Q13": "No", "Q14": "No", "Q15": "Yes"}), cfg)
    mods = {m["id"]: m for m in res["optional_modules"]}
    assert mods["vmc"]["enabled"] and not mods["sap"]["enabled"] and not mods["app-modernization"]["enabled"] and mods["saas"]["enabled"]
    assert not mods["storage"]["enabled"]                      # the gate was not answered
    sap = [t for t in res["text_answers"] if t["module"] == "sap"]
    assert sap and not any(t["included"] for t in sap)


def test_activities_below_the_threshold_get_actions_the_rest_do_not(cfg, all3, rset):
    r = {**all3, "Q26": 4, "Q29": 4, "Q33": 4}      # High Level Business Case is 4.0, everything else 3
    res = compute_result(rset(r), cfg)
    bc1 = next(a for a in res["activities"] if a["id"] == "BC-1")
    assert not bc1["needs_action"] and bc1["actions"] == [] and bc1["colour"] == "green"
    bc2 = next(a for a in res["activities"] if a["id"] == "BC-2")
    assert bc2["needs_action"] and bc2["actions"][0]["source"] == "library" and bc2["actions"][0]["library_id"] == "AL-01"
    se2 = next(a for a in res["activities"] if a["id"] == "SE-2")     # no library entry: the next-level wording is used
    assert se2["actions"][0]["source"] == "next_level" and "Q74" in se2["actions"][0]["text"]


def test_the_observation_is_the_questionnaires_own_wording(cfg, all3, rset):
    res = compute_result(rset(all3), cfg)
    a = next(x for x in res["activities"] if x["id"] == "BC-4")
    assert a["observation"]["source"] == "tool"
    assert a["observation"]["text"] == cfg["qmap"]["Q25"].levels[2].split(" - ", 1)[1].strip()


def test_the_fingerprint_changes_with_scores_and_not_with_wording(cfg, golden, rset):
    a = compute_result(rset(golden["ratings"]), cfg)
    b = compute_result(rset(golden["ratings"]), cfg)
    b["narrative"] = {"text": "different words"}
    assert scores_fingerprint(a) == scores_fingerprint(b)
    c = compute_result(rset({**golden["ratings"], "Q26": 1}), cfg)
    assert scores_fingerprint(a) != scores_fingerprint(c)
