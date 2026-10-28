from mra.loader import load_config
from tests.conftest import ROOT


def test_the_live_config_folder_is_consistent():
    """Whatever is in config/ must load; the other tests use the frozen copy in tests/fixtures."""
    cfg = load_config(ROOT / "config")
    assert len(cfg["questions"]) >= 1 and cfg["config_hash"]
