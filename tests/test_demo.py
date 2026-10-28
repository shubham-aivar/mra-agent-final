from pathlib import Path

from scripts.demo import main


def test_the_demo_runs_end_to_end_and_writes_the_files(tmp_path, capsys):
    assert main(["--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Incomplete" in out and "STEP 7b" in out and "chain intact" in out
    files = sorted(p.name for p in next(tmp_path.glob("run-*")).joinpath("files").iterdir())
    assert files == ["MRA_demo_FINAL.docx", "MRA_demo_FINAL.xlsx", "MRA_demo_FINAL_planning_context.md"]
    assert Path("samples/sample_questionnaire.xlsx").exists()
