"""Build the folder CloudFront serves: the page's files, with the page told it is the hosted version (no local token).
Run from the project root: python scripts/build_site.py [--out build/site]. config.json (the Cognito settings) is added by the deployment itself.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "mra" / "ui" / "static"


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "static").mkdir()                    # the page asks for /static/app.js and /static/style.css, as the local server serves them
    for name in ("app.js", "style.css"):
        shutil.copy2(STATIC / name, out / "static" / name)
    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__MRA_TOKEN__", "")
    (out / "index.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "build" / "site"))
    a = ap.parse_args()
    build(Path(a.out))
    print("Built", a.out)
