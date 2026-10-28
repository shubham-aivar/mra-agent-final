"""Build the folder the CDK stack deploys: the agent code, the config, and Linux ARM64 builds of the
two packages the exports need. Run from the project root: python scripts/build_lambda.py [--skip-deps]

boto3 is already in the Lambda runtime, so it is not bundled.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPS = ["openpyxl", "python-docx", "python-pptx", "pillow"]


def build(out: Path, skip_deps: bool = False) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ("mra", "hosted", "config"):
        shutil.copytree(ROOT / name, out / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if not skip_deps:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(out), "--platform",
                        "manylinux2014_aarch64", "--python-version", "3.12", "--implementation", "cp",
                        "--only-binary=:all:", *DEPS], check=True)
    for p in out.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "build" / "lambda"))
    ap.add_argument("--skip-deps", action="store_true", help="copy code only (for tests and dry runs)")
    a = ap.parse_args()
    build(Path(a.out), a.skip_deps)
    print("Built", a.out)
