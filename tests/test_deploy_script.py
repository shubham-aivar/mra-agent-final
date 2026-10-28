import subprocess

from tests.conftest import ROOT

SCRIPT = str(ROOT / "scripts" / "deploy.sh")


def run(*args):
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, cwd=ROOT, timeout=60)


def test_a_dry_run_shows_every_step_in_order_and_changes_nothing():
    r = run("--dry-run", "--account", "123456789012", "--region", "eu-west-1", "--model-id", "eu.anthropic.claude-test", "--profile", "aivar-vision-admin")
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("[dry run]")]
    order = ["pip install", "build_lambda.py", "build_site.py", "cdk", "bootstrap", "cdk", "deploy"]
    text = "\n".join(lines)
    pos = [text.index(k) for k in ("pip install", "build_lambda.py", "build_site.py", "bootstrap aws://123456789012/eu-west-1", "deploy MraAgent")]
    assert pos == sorted(pos) and order
    assert "-c account=123456789012 -c region=eu-west-1 -c model_id=eu.anthropic.claude-test" in text and "--outputs-file build/cdk-outputs.json" in text
    assert "Profile: aivar-vision-admin" in r.stdout and "Use synthetic data only" in r.stdout
    assert "add_user.py" in r.stdout and "smoke_test.py" in r.stdout and "--username mra-analyst" in r.stdout
    assert f"--app '{ROOT}/.venv-deploy/bin/python3' -m infra.app" in text                       # the exact interpreter, quoted
    assert f"[dry run] {ROOT}/.venv-deploy/bin/python3 -m pip install -q -r requirements-hosted.txt" in text


def test_unknown_options_are_refused_and_help_works():
    assert run("--nonsense").returncode == 2 and "Unknown option" in run("--nonsense").stdout
    h = run("--help")
    assert h.returncode == 0 and "--dry-run" in h.stdout


def test_the_script_is_valid_shell_and_executable():
    assert subprocess.run(["bash", "-n", SCRIPT]).returncode == 0
    assert (ROOT / "scripts" / "deploy.sh").stat().st_mode & 0o111
