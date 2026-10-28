import json

import pytest

from mra.cli import main
from tests.conftest import FIXTURES, write_questionnaire


@pytest.fixture
def env(tmp_path, cfg, golden, monkeypatch):
    for k in ("MRA_LLM_MODE", "MRA_STORAGE_MODE", "MRA_BUCKET", "MRA_LOCAL_ROOT"):
        monkeypatch.delenv(k, raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"llm": {"mode": "stub"}, "storage": {"mode": "local", "local_root": str(tmp_path / "store")}}))
    q = write_questionnaire(tmp_path / "travel co.xlsx", cfg, golden["ratings"], {1: "scope text"})
    base = ["--config", str(FIXTURES / "config"), "--settings", str(settings), "--as", "tc"]
    return {"base": base, "q": str(q), "out": str(tmp_path / "out"), "tmp": tmp_path}


def cli(env, *args):
    return main([*env["base"], *args])


def test_run_prints_the_results_and_a_blank_is_called_out(env, capsys):
    assert cli(env, "run", env["q"], "--set-id", "r1", "--customer", "Customer A") == 0
    out = capsys.readouterr().out
    assert "People Incomplete" in out and "BLANK RATINGS: Q42" in out and "Activities needing an action: 24" in out
    assert "Stored under" in out and "Summary [stub]" in out


def test_the_set_id_comes_from_the_file_name_when_not_given(env, capsys):
    cli(env, "run", env["q"])
    assert "travel-co" in capsys.readouterr().out


def test_status_adjust_and_confirm_walk_the_gate(env, capsys):
    cli(env, "run", env["q"], "--set-id", "r1")
    capsys.readouterr()
    assert cli(env, "confirm", "r1") == 2 and "blank" in capsys.readouterr().out
    assert cli(env, "adjust", "r1", "--question", "Q42", "--score", "1", "--reason", "no cloud team per the CIO") == 0
    assert "People 2.8" in capsys.readouterr().out
    assert cli(env, "confirm", "r1") == 0
    assert cli(env, "status", "r1") == 0
    assert "scores confirmed: True" in capsys.readouterr().out
    assert cli(env, "finalise", "r1") == 3 and "Cannot finalise yet" in capsys.readouterr().out


def test_input_problems_exit_2_and_change_nothing(env, capsys, cfg, golden):
    bad = write_questionnaire(env["tmp"] / "bad.xlsx", cfg, golden["ratings"], overrides={26: {"Response": "3.5"}})
    assert cli(env, "run", str(bad), "--set-id", "bad") == 2
    assert "must be a whole number" in capsys.readouterr().out
    assert cli(env, "status", "bad") == 2


def test_meta_and_export_write_the_files(env, capsys):
    cli(env, "run", env["q"], "--set-id", "r1", "--facilitator", "Ingram Micro")
    assert cli(env, "meta", "r1", "--workshop-date", "2026-10-23", "--readout-date", "2026-10-30") == 0
    assert "'readout_date': '2026-10-30'" in capsys.readouterr().out
    assert cli(env, "export", "r1", "--xlsx", "--docx", "--context", "--out", env["out"]) == 0
    names = sorted(p.name for p in (env["tmp"] / "out").iterdir())
    assert names == ["MRA_r1_DRAFT.docx", "MRA_r1_DRAFT.xlsx", "MRA_r1_DRAFT_planning_context.md"]
    capsys.readouterr()
    assert cli(env, "export", "r1", "--pptx", "--out", env["out"]) == 0
    assert "Ingram MAP deliverable (built in)" in capsys.readouterr().out and (env["tmp"] / "out" / "MRA_r1_DRAFT.pptx").exists()
    assert cli(env, "export", "r1", "--pptx", "--pptx-template", str(env["tmp"] / "nope.pptx"), "--out", env["out"]) == 2


def test_agent_proposals_decisions_kpis_audit_and_purge(env, capsys):
    cli(env, "run", env["q"], "--set-id", "r1")
    assert cli(env, "agent", "r1", "--task", "rating") == 0 and "does not judge ratings" in capsys.readouterr().out
    assert cli(env, "proposals", "r1") == 0 and "0 proposals" in capsys.readouterr().out
    assert cli(env, "decide-proposals", "r1", "--accept", "P0001") == 2 and "no proposals yet" in capsys.readouterr().out
    assert cli(env, "log-hours", "r1", "--hours", "2.5") == 0
    assert cli(env, "kpis", "r1") == 0 and "Analyst hours logged: 2.5" in capsys.readouterr().out
    assert cli(env, "audit", "r1", "--show") == 0 and "chain intact" in capsys.readouterr().out
    assert cli(env, "purge", "r1") == 2
    assert cli(env, "purge", "r1", "--confirm") == 0 and cli(env, "status", "r1") == 2


def test_decide_and_resolve_use_activity_and_item_keys(env, capsys):
    cli(env, "run", env["q"], "--set-id", "r1")
    assert cli(env, "decide", "r1", "--activity", "BC-1", "--decision", "approve", "--owner", "A", "--due", "2026-12-15") == 0
    assert cli(env, "decide", "r1", "--activity", "BC-1", "--decision", "reject") == 2
    assert cli(env, "resolve", "r1", "--item", "no_evidence:Q23", "--resolution", "accepted_risk", "--note", "seen in workshop") == 0
    assert cli(env, "resolve", "r1", "--item", "blank_rating:Q42", "--resolution", "resolved", "--note", "x") == 2
    assert "give it a rating" in capsys.readouterr().out


def test_import_questions_writes_a_bank(env, capsys, cfg, tmp_path):
    t = write_questionnaire(tmp_path / "template.xlsx", cfg, {})
    out = tmp_path / "bank.json"
    assert cli(env, "import-questions", str(t), "--version", "v9", "--out", str(out)) == 0
    assert json.loads(out.read_text())["version"] == "v9" and "Imported 162 questions" in capsys.readouterr().out


def test_check_template_reports_what_can_be_filled(env, capsys, tmp_path):
    assert cli(env, "check-template") == 0
    out = capsys.readouterr().out
    assert "Charts (scores and radars)" in out and "20 of 20" in out and "Every part can be filled." in out
    from pptx import Presentation
    p = tmp_path / "plain.pptx"
    Presentation().save(p)
    assert cli(env, "check-template", str(p)) == 3 and "MISSING" in capsys.readouterr().out
    assert cli(env, "check-template", str(tmp_path / "nope.pptx")) == 2
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"junk")
    assert cli(env, "check-template", str(bad)) == 2


def test_make_template_never_overwrites_and_enforces_the_leak_check(env, capsys, tmp_path):
    from mra.outputs.deck_template import default_template_bytes
    src = tmp_path / "filled.pptx"
    src.write_bytes(default_template_bytes())
    out = tmp_path / "clean.pptx"
    assert cli(env, "make-template", str(src), "--out", str(out)) == 0 and out.exists() and "none remain" in capsys.readouterr().out
    assert cli(env, "make-template", str(src), "--out", str(out)) == 2 and "already exists" in capsys.readouterr().out
    assert cli(env, "make-template", str(src), "--out", str(tmp_path / "x.pptx"), "--forbid", "MRA Readiness Scores") == 2
    assert "Not written" in capsys.readouterr().out and not (tmp_path / "x.pptx").exists()
    assert cli(env, "make-template", str(tmp_path / "missing.pptx"), "--out", str(tmp_path / "y.pptx")) == 2
