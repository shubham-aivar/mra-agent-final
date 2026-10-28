"""The review page's helper functions (no HTTP)."""
import getpass

import pytest

from mra.ui.api import UiApi, UiError, _excerpt, clean_actor, find_soffice


def test_an_excerpt_finds_the_quote_whatever_the_spacing_or_case():
    text = "A" * 300 + "  The CIO said   moving to the CLOUD is top priority.  " + "B" * 300
    ex = _excerpt(text, "the cio said moving to the cloud is top priority.")
    assert ex["found"] and ex["quote"].startswith("The CIO said") and ex["quote"].endswith("priority.")
    assert ex["before"].startswith("\u2026") and ex["after"].endswith("\u2026")          # both sides were cut
    short = _excerpt("only this", "only this")
    assert short["before"] == "" and short["after"] == ""                                   # nothing was cut: no ellipsis


def test_a_quote_that_cannot_be_found_is_returned_alone():
    ex = _excerpt("some note", "words that are not there")
    assert ex == {"found": False, "before": "", "quote": "words that are not there", "after": ""}
    assert _excerpt("note", "")["found"] is False


@pytest.mark.parametrize("raw,want", [("Alice", "Alice"), ("  Dr. O'Neil-Smith  ", "Dr. O'Neil-Smith"), ("a@b.co", "a@b.co"),
                                      ("<script>x</script>", "scriptxscript"), ("x" * 100, "x" * 60)])
def test_the_actor_name_keeps_only_safe_characters(raw, want):
    assert clean_actor(raw) == want


def test_an_empty_actor_falls_back_to_the_os_user():
    assert clean_actor(None) == getpass.getuser() and clean_actor("  ;;  ") == getpass.getuser()


def test_libreoffice_is_found_through_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda *_: None)
    monkeypatch.setenv("MRA_SOFFICE", str(tmp_path / "missing"))
    assert find_soffice() is None
    fake = tmp_path / "soffice"
    fake.write_text("x")
    monkeypatch.setenv("MRA_SOFFICE", str(fake))
    assert find_soffice() == str(fake)


def test_a_deck_that_cannot_be_read_is_refused_and_a_partial_one_is_stored_with_its_coverage(svc):
    api = UiApi(svc)
    with pytest.raises(UiError) as e:
        api._store_pptx(b"not a deck", "x.pptx")
    assert e.value.status == 422 and "cannot be filled" in e.value.payload["error"]
    from io import BytesIO

    from pptx import Presentation
    buf = BytesIO()
    Presentation().save(buf)
    entry = api._store_pptx(buf.getvalue(), "plain.pptx")
    assert len(entry["id"]) == 10 and not entry["coverage"]["complete"] and entry["coverage"]["found"] == 0
    assert api._store_pptx(buf.getvalue(), "renamed.pptx")["id"] == entry["id"]                  # the same bytes are one entry


def test_invalid_run_ids_never_reach_storage(svc):
    api = UiApi(svc)
    for bad in ("../x", "a/b", "", ".hidden", "x" * 80):
        with pytest.raises(UiError):
            api.view(bad)
