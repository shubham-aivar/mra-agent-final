"""scripts/smoke_test.py against the stand-in AWS path: the script a person runs against the real deployment is itself tested here."""
import os
import subprocess
import sys

import pytest

from tests.conftest import ROOT
from tests.hosted_harness import Harness

SCRIPT = str(ROOT / "scripts" / "smoke_test.py")


@pytest.fixture(scope="module")
def harness(cfg, tmp_path_factory):
    h = Harness(cfg, tmp_path_factory.mktemp("h")).start()
    yield h
    h.stop()


def smoke(h, user, password, *extra, timeout=240):
    env = {**os.environ, "MRA_SMOKE_PASSWORD": password}
    return subprocess.run([sys.executable, SCRIPT, "--url", h.url, "--username", user, "--expect-engine", "stub", *extra],
                          env=env, capture_output=True, text=True, timeout=timeout, cwd=ROOT)


def test_a_healthy_deployment_passes_every_check_and_leaves_nothing_behind(harness):
    r = smoke(harness, "demo", "Demo#Pass12345")
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("the page is served with its security headers", "the API refuses a request with no token", "sign in with a username and password",
                 "upload to storage, then score as a background job", "an agent task runs as a background job",
                 "Excel, Word and PowerPoint are created and downloaded through signed links", "the smoke run is deleted"):
        assert f"PASS  {name}" in r.stdout, name
    assert "0 failed" in r.stdout and "FAIL" not in r.stdout
    assert [x for x in harness.svc.s.list("runs/") if "/smoke-" in x] == []                    # the run and its files were deleted


def test_a_wrong_password_stops_at_sign_in_with_a_clear_message(harness):
    r = smoke(harness, "demo", "Wrong#Pass12345")
    assert r.returncode == 1 and "FAIL  sign in with a username and password" in r.stdout and "Incorrect username or password" in r.stdout
    assert "upload to storage" not in r.stdout                                                  # nothing is attempted after a failed sign-in


def test_a_read_only_user_is_told_to_use_a_different_account(harness):
    r = smoke(harness, "reader", "Reader#Pass12345")
    assert r.returncode == 1 and "FAIL  the signed-in user may change things" in r.stdout and "use a user made without --reader" in r.stdout
    assert "upload to storage" not in r.stdout and "1 failed" in r.stdout                       # one clear failure, not a pile of consequences


def test_a_new_account_that_must_change_its_password_is_explained(harness):
    r = smoke(harness, "fresh", "Temp#12345")
    assert r.returncode == 1 and "choose a new password" in r.stdout and "--permanent" in r.stdout


def test_the_wrong_engine_is_reported(harness):
    env = {**os.environ, "MRA_SMOKE_PASSWORD": "Demo#Pass12345"}
    r = subprocess.run([sys.executable, SCRIPT, "--url", harness.url, "--username", "demo", "--skip-agent"], env=env, capture_output=True, text=True,
                       timeout=120, cwd=ROOT)
    assert r.returncode == 1 and "the text engine is 'stub', expected 'bedrock'" in r.stdout


def test_a_url_that_is_not_the_site_fails_the_first_check(harness):
    env = {**os.environ, "MRA_SMOKE_PASSWORD": "x"}
    r = subprocess.run([sys.executable, SCRIPT, "--url", "http://127.0.0.1:9", "--username", "demo"], env=env, capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 1 and "FAIL  the page is served" in r.stdout
