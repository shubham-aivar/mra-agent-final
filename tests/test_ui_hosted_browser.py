"""The hosted page in a real browser (Playwright + Chromium), against the stand-in AWS path in tests/hosted_harness.py. Skipped when they are not installed.
Set MRA_SHOTS=<folder> to keep screenshots."""
import os
import subprocess

import pytest

from mra.outputs.deck_template import default_template_bytes
from tests.conftest import ROOT, write_questionnaire
from tests.helpers import NOTE
from tests.hosted_harness import Harness
from tests.test_ui_browser import _playwright_ready

SCRIPT = ROOT / "tests" / "ui_browser" / "hosted_test.js"


@pytest.mark.skipif(_playwright_ready() is not None, reason=_playwright_ready() or "")
def test_the_hosted_page_in_a_real_browser(cfg, golden, tmp_path):
    q = write_questionnaire(tmp_path / "customer questionnaire.xlsx", cfg, golden["ratings"], {1: "scope text", 22: "There is no cloud team"})
    notes = tmp_path / "cio.md"
    notes.write_text(NOTE + "\n")
    deck = tmp_path / "AWS deck.pptx"
    deck.write_bytes(default_template_bytes())
    out = tmp_path / "out"
    out.mkdir()
    h = Harness(cfg, tmp_path).start()
    try:
        env = {**os.environ, "MRA_URL": h.url, "MRA_S3_PORT": str(h.moto_port), "MRA_QUESTIONNAIRE": str(q), "MRA_NOTES": str(notes),
               "MRA_DECK": str(deck), "MRA_OUT": str(out)}
        r = subprocess.run(["node", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=900)
    finally:
        h.stop()
    print(r.stdout, r.stderr[-1500:])
    assert r.returncode == 0, r.stdout[-3500:] + r.stderr[-800:]
