"""The deck's 'areas of focus' sentences and strength/gap lists, reproduced from the real deck's own text."""
import copy

from mra.focus import ascending, compute_focus, strongest, weakest
from mra.pipeline import compute_result


def aws_view(res):
    """The AWS tool counts a blank-containing group as 0. Present the golden result that way to compare with the deck."""
    g = {k: copy.deepcopy(res[k]) for k in ("perspectives", "workstreams", "categories", "activities")}
    for rows in g.values():
        for x in rows:
            if x["status"] == "incomplete":
                x.update(status="complete", score=0.0)
    return g


def test_perspective_sentences_equal_the_deck(cfg, golden, rset):
    f = compute_focus(aws_view(compute_result(rset(golden["ratings"]), cfg)), cfg)
    assert f["perspective_sentences"] == [
        "You're already doing very well in the Operations dimension; your definitive strength lies in Asset Management, where you're already well-prepared.",
        "We estimate the most initial effort in the People dimension; we should especially focus on Organizational Structure and improve your capabilities.",
        "In addition to that, in the Security dimension, DevSecOps Principles should receive some attention as well; so let's get started!"]


def test_workstream_sentences_equal_the_deck(cfg, golden, rset):
    f = compute_focus(aws_view(compute_result(rset(golden["ratings"]), cfg)), cfg)
    assert f["workstream_sentences"] == [
        "You're already excelling in the Migration Process & Experience dimension. Keep up the great work!",
        "Put emphasis on improving the Skills & COE dimension to ensure you're well-prepared for a steady and successful cloud migration.",
        "Furthermore, be deliberate in boosting your capabilities in the Application Portfolio Discovery & Planning dimension."]


def test_strengths_and_gaps_equal_the_executive_summary_slide(cfg, golden, rset):
    f = compute_focus(aws_view(compute_result(rset(golden["ratings"]), cfg)), cfg)
    assert f["strengths"] == ["Managed Service Provider Identified", "Project Management Capability"]
    assert f["gaps"] == ["Current Operations Model", "Workload Owner Buy-In or Alignment", "Key Stakeholder Sign-off",
                         "Migration Funding Commitment"]


def test_key_observations_equal_the_two_deck_slides(cfg, golden, rset):
    pages = compute_focus(aws_view(compute_result(rset(golden["ratings"]), cfg)), cfg)["key_observations"]
    assert pages[0]["strong"] == ["Migration Experience", "Account Design & Configuration"]
    assert pages[0]["weak"] == ["Design or Evolve COE", "Workload Owner Buy-In or Alignment", "Key Stakeholder Sign-off"]
    assert pages[1]["strong"] == ["Service Catalog", "Config Management & Change Management"]
    assert pages[1]["weak"] == ["Current Operations Model", "BCP/DR", "DevSecOps Principles"]


def test_ties_the_strongest_is_the_last_in_questionnaire_order_the_weakest_the_first():
    rows = [{"name": n, "status": "complete", "score": s} for n, s in (("a", 4), ("b", 2), ("c", 4), ("d", 2))]
    assert [r["name"] for r in strongest(rows, 2)] == ["c", "a"]
    assert [r["name"] for r in weakest(rows, 2)] == ["b", "d"]


def test_incomplete_groups_are_left_out_never_treated_as_zero(cfg, golden, rset):
    res = compute_result(rset(golden["ratings"]), cfg)
    assert all(g["status"] == "complete" for g in ascending(res["perspectives"]))
    f = res["focus"]
    assert "People" in f["incomplete"] and "Skills & COE" in f["incomplete"]
    assert not any("People" in s for s in f["perspective_sentences"][1:])
    assert "Design or Evolve COE" not in f["key_observations"][0]["weak"]


def test_rating_the_blank_brings_people_back_as_the_weakest(cfg, full_ratings, rset):
    res = compute_result(rset(full_ratings), cfg)
    f = compute_focus({k: res[k] for k in ("perspectives", "workstreams", "categories", "activities")}, cfg)
    assert "People dimension" in f["perspective_sentences"][1] and "Organizational Structure" in f["perspective_sentences"][1]
