"""Drive the real page in a real browser (Playwright + Chromium) through a whole review. Skipped when they are not installed.

Needs: node, and `npm install -g playwright` with its Chromium (`npx playwright install chromium`).
Set MRA_TEMPLATE_DECK to use the real AWS deck as the template; MRA_BROWSER_TEST_PREVIEW=1 to also test the LibreOffice preview.
Set MRA_UI_SHOTS=<folder> to keep screenshots.
"""
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from mra.ui import api as ui_api
from mra.ui.server import make_server
from tests.conftest import ROOT, write_questionnaire
from tests.helpers import NOTE
from tests.test_outputs import template_bytes  # noqa: F401

SCRIPT = ROOT / "tests" / "ui_browser" / "browser_test.js"


def _playwright_ready() -> str | None:
    node = shutil.which("node")
    if not node:
        return "node is not installed"
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
        probe = subprocess.run([node, "-e", f"const {{chromium}}=require('{root}/playwright');(async()=>{{const b=await chromium.launch();await b.close()}})()"],
                               capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"cannot check Playwright: {e}"
    return None if probe.returncode == 0 else "Playwright or its Chromium is not installed"


@pytest.mark.skipif(_playwright_ready() is not None, reason=_playwright_ready() or "")
def test_a_whole_review_in_a_real_browser(svc, cfg, golden, tmp_path, monkeypatch, template_bytes):  # noqa: F811
    preview = os.environ.get("MRA_BROWSER_TEST_PREVIEW") == "1"
    if not preview:
        monkeypatch.setattr(ui_api, "find_soffice", lambda: None)
    q = write_questionnaire(tmp_path / "customer questionnaire.xlsx", cfg, golden["ratings"],
                            {1: "scope text", 3: '<img src=x onerror="window.__xss3=1">', 22: "There is no cloud team"})
    notes = tmp_path / "cio.md"
    notes.write_text(NOTE + "\n<script>window.__xss2=1</script>\n")
    deck = tmp_path / "AWS deck.pptx"
    real = os.environ.get("MRA_TEMPLATE_DECK")
    deck.write_bytes(Path(real).read_bytes() if real else template_bytes)
    out = tmp_path / "out"
    out.mkdir()
    srv = make_server(svc, 0, "browser-token")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "MRA_URL": f"http://127.0.0.1:{srv.server_address[1]}/", "MRA_QUESTIONNAIRE": str(q), "MRA_NOTES": str(notes),
              "MRA_DECK": str(deck), "MRA_OUT": str(out), "MRA_EXPECT_PREVIEW": "1" if preview else "0",
               "MRA_REAL_DECK": "1" if real else "0"}
        r = subprocess.run(["node", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=900)
    finally:
        srv.shutdown()
        srv.server_close()
    print(r.stdout, r.stderr)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
