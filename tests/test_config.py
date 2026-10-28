import copy
import json
import shutil

import pytest

from mra.loader import InputValidationError, load_config
from tests.conftest import FIXTURES

_n = [0]


def _edit(tmp_path, name, fn):
    _n[0] += 1
    d = tmp_path / f"cfg{_n[0]}"
    shutil.copytree(FIXTURES / "config", d)
    p = d / f"{name}.json"
    data = json.loads(p.read_text())
    fn(data)
    p.write_text(json.dumps(data))
    return d


def test_the_aws_questionnaire_is_loaded_completely(cfg):
    qs = cfg["questions"]
    assert len(qs) == 162
    assert sum(q.type == "rating" and q.perspective is not None for q in qs) == 62
    assert {q.type for q in qs} == {"rating", "yes_no", "text"}
    assert [p["id"] for p in cfg["perspectives"]] == ["business", "platform", "people", "governance", "operations", "security"]
    assert len(cfg["activities"]) == 34 and len(cfg["workstreams"]) == 8
    assert sum(a["phase"] == "Pre-Mobilize" for a in cfg["activities"]) == 14
    assert len(cfg["modules"]) == 9
    assert all(len(q.levels) == 5 for q in qs if q.type == "rating")


def test_every_scored_question_belongs_to_exactly_one_activity(cfg):
    scored = {q.id for q in cfg["questions"] if q.type == "rating" and q.perspective}
    assert set(cfg["activity_of"]) == scored


def test_inferred_and_proposed_files_are_listed_for_the_reports(cfg):
    notes = " ".join(cfg["placeholder_notes"])
    assert "rubric" in notes and "workstreams" in notes and "action_library" in notes


def test_config_hash_is_stable_and_changes_with_content(cfg, tmp_path):
    assert load_config(FIXTURES / "config")["config_hash"] == cfg["config_hash"]
    d = _edit(tmp_path, "rubric", lambda r: r.update(action_below=3.5))
    assert load_config(d)["config_hash"] != cfg["config_hash"]


def test_a_scored_question_without_an_activity_is_refused(tmp_path):
    def drop(ws):
        ws["workstreams"][0]["activities"][0]["questions"].pop()
    with pytest.raises(InputValidationError, match="not mapped to any activity"):
        load_config(_edit(tmp_path, "workstreams", drop))


def test_a_question_in_two_activities_is_refused(tmp_path):
    def dup(ws):
        ws["workstreams"][0]["activities"][1]["questions"].append(ws["workstreams"][0]["activities"][0]["questions"][0])
    with pytest.raises(InputValidationError, match="mapped to two activities"):
        load_config(_edit(tmp_path, "workstreams", dup))


def test_rubric_thresholds_are_checked(tmp_path):
    """F67: nonsense thresholds used to load silently."""
    with pytest.raises(InputValidationError, match="red_below must be below"):
        load_config(_edit(tmp_path, "rubric", lambda r: r["colours"].update(red_below=4.5)))
    with pytest.raises(InputValidationError, match="green_from must be a number"):
        load_config(_edit(tmp_path, "rubric", lambda r: r["colours"].update(green_from="four")))
    with pytest.raises(InputValidationError, match="scale_min must be below"):
        load_config(_edit(tmp_path, "rubric", lambda r: r.update(scale_min=5)))


def test_a_rating_question_needs_five_levels(tmp_path):
    def cut(b):
        next(q for q in b["questions"] if q["type"] == "rating")["levels"].pop()
    with pytest.raises(InputValidationError, match="exactly 5 level descriptions"):
        load_config(_edit(tmp_path, "question_bank", cut))


def test_a_missing_field_is_reported_not_a_traceback(tmp_path):
    def cut(b):
        del b["questions"][0]["text"]
    with pytest.raises(InputValidationError, match="missing or bad field"):
        load_config(_edit(tmp_path, "question_bank", cut))


def test_unknown_module_gate_and_unknown_action_activity_are_refused(tmp_path):
    with pytest.raises(InputValidationError, match="gate question"):
        load_config(_edit(tmp_path, "question_bank", lambda b: b["modules"][0].update(gate="Q999")))
    with pytest.raises(InputValidationError, match="unknown activity"):
        load_config(_edit(tmp_path, "action_library", lambda a: a["actions"][0]["activities"].append("ZZ-9")))


def test_duplicate_ids_are_reported_together(tmp_path):
    def dup(b):
        b["questions"].append(copy.deepcopy(b["questions"][0]))
        b["areas"].append(copy.deepcopy(b["areas"][0]))
    with pytest.raises(InputValidationError) as e:
        load_config(_edit(tmp_path, "question_bank", dup))
    assert len(e.value.errors) >= 2
