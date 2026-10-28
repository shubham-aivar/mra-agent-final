"""The scoring rules, checked against the AWS tool's own outputs for a real engagement (the golden fixture)."""
import pytest

from mra.pipeline import compute_result
from mra.scoring import colour_for, round_half_up


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_every_group_matches_the_aws_scores_sheet(cfg, golden, rset):
    """41 sheet values: 6 perspectives and 35 categories. A blank-containing group is Incomplete here; AWS shows 0."""
    res = compute_result(rset(golden["ratings"]), cfg)
    sheet = golden["scores_sheet"]
    areas = {a["id"]: a for a in cfg["areas"]}
    checked = 0
    for p in res["perspectives"]:
        expected = float(sheet[f"{p['name']}|"])
        assert (p["score"] if p["status"] == "complete" else p["aws_equivalent"]) == expected, p["name"]
        checked += 1
    for c in res["categories"]:
        expected = float(sheet[f"{areas[c['id']]['section']}|{c['name']}"])
        assert (c["score"] if c["status"] == "complete" else c["aws_equivalent"]) == expected, c["name"]
        checked += 1
    assert checked == 41


def test_every_activity_and_workstream_matches_the_deck_charts(cfg, golden, rset):
    res = compute_result(rset(golden["ratings"]), cfg)
    ws = {w["id"]: w for w in res["workstreams"]}
    for key, expected in golden["activity_score"].items():
        wid, aid = key.split("/")
        a = next(x for x in res["activities"] if x["id"] == aid)
        assert a["workstream"] == wid
        assert (a["score"] if a["status"] == "complete" else a["aws_equivalent"]) == expected, a["name"]
    for name, expected in golden["workstream_score"].items():
        w = next(x for x in ws.values() if x["name"] == name)
        assert (w["score"] if w["status"] == "complete" else w["aws_equivalent"]) == expected, name


def test_radar_values_match_the_deck_exactly(cfg, golden, rset):
    """Radar = mean rounded to 3 decimals, times 20. This is where 63.34 (not 63.33) comes from."""
    res = compute_result(rset(golden["ratings"]), cfg)
    for p in res["perspectives"]:
        exp = golden["perspective_radar"][p["name"]]
        assert (p["radar"] if p["radar"] is not None else 0.0) == pytest.approx(exp, abs=1e-9)
    for w in res["workstreams"]:
        exp = golden["workstream_radar"][w["name"]]
        assert (w["radar"] if w["radar"] is not None else 0.0) == pytest.approx(exp, abs=1e-9)


def test_the_blank_rating_makes_exactly_its_groups_incomplete(cfg, golden, rset):
    res = compute_result(rset(golden["ratings"]), cfg)
    assert res["blank_ratings"] == ["Q42"]
    assert [p["name"] for p in res["perspectives"] if p["status"] == "incomplete"] == ["People"]
    assert [w["name"] for w in res["workstreams"] if w["status"] == "incomplete"] == ["Skills & COE"]
    assert [a["name"] for a in res["activities"] if a["status"] == "incomplete"] == ["Design or Evolve COE"]
    people = next(p for p in res["perspectives"] if p["name"] == "People")
    assert people["score"] is None and people["radar"] is None and people["colour"] == "incomplete"
    assert people["aws_equivalent"] == 0.0           # kept for back-testing only


def test_rating_the_blank_completes_the_groups(cfg, full_ratings, rset):
    res = compute_result(rset(full_ratings), cfg)
    assert res["blank_ratings"] == []
    assert all(g["status"] == "complete" for k in ("perspectives", "workstreams", "activities") for g in res[k])
    assert next(p for p in res["perspectives"] if p["name"] == "People")["score"] == 2.8
    assert next(w for w in res["workstreams"] if w["name"] == "Skills & COE")["score"] == 3.0


def test_rounding_is_half_up_not_the_float_default():
    assert round_half_up(3.25, 1) == 3.3 and round(3.25, 1) == 3.2
    assert round_half_up(2.5, 0) == 3.0
    assert round_half_up(3.1666, 3) == 3.167
    assert round_half_up(63.34, 2) == 63.34


def test_a_perspective_is_the_mean_of_its_questions_not_of_its_categories(cfg, all3, rset):
    r = dict(all3)
    r["Q23"] = 5          # Business has 13 questions; Q23 is the only one in its category
    res = compute_result(rset(r), cfg)
    biz = _by_name(res["perspectives"])["Business"]
    assert biz["score"] == round_half_up((3 * 12 + 5) / 13, 1)


def test_colours_follow_the_configured_thresholds(cfg):
    rub = cfg["rubric"]
    assert colour_for(4.0, rub) == "green" and colour_for(3.9, rub) == "amber" and colour_for(0.5, rub) == "amber"
    assert colour_for(None, rub) == "incomplete"
    red = {**rub, "colours": {"green_from": 4.0, "red_below": 2.0}}
    assert colour_for(1.9, red) == "red" and colour_for(2.0, red) == "amber"


def test_a_reviewer_rating_replaces_the_original_and_keeps_it(cfg, golden, rset):
    ov = {"Q26": {"score": 2, "reason": "workshop note", "by": "tc", "at": "t"}}
    res = compute_result(rset(golden["ratings"]), cfg, ov)
    q = next(x for x in res["questions"] if x["question_id"] == "Q26")
    assert (q["score"], q["original_score"], q["adjusted"]) == (2, 4, True)
    assert res["adjustments"][0]["reason"] == "workshop note"


def test_the_level_text_follows_the_rating(cfg, golden, rset):
    res = compute_result(rset(golden["ratings"]), cfg)
    q = next(x for x in res["questions"] if x["question_id"] == "Q26")
    assert q["level_text"] == cfg["qmap"]["Q26"].levels[3] and q["next_level_text"] == cfg["qmap"]["Q26"].levels[4]
    five = compute_result(rset({"Q26": 5}), cfg)
    assert next(x for x in five["questions"] if x["question_id"] == "Q26")["next_level_text"] is None


def test_the_result_is_deterministic(cfg, golden, rset):
    a, b = compute_result(rset(golden["ratings"]), cfg), compute_result(rset(golden["ratings"]), cfg)
    assert a == b


def test_optional_module_ratings_never_block_or_join_the_six(cfg, golden, rset):
    res = compute_result(rset(golden["ratings"]), cfg)
    assert {m["id"] for m in res["modules"]} == {"outpost", "saas"}
    assert all(m["status"] == "incomplete" for m in res["modules"])
    assert not any(i["ref"] in ("Q134", "Q158") for i in res["validation_items"])
