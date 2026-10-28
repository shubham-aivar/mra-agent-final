"""Kept for convenience: `python scripts/run_cli.py ...` is the same as `python -m mra ...`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mra.cli import main  # noqa: E402

sys.exit(main())
