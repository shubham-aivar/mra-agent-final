"""The deck as it looks when rendered: no text on top of text, nothing running into the layout's right-hand stripe. The template's own overlaps
(a few labels that touch in some renderers) are measured first and are not counted against us. Needs LibreOffice and pdftotext; skipped without them."""
import pytest

from mra.outputs import render
from mra.outputs.ingram_deck import ingram_template_bytes
from tests import render_check as rc
from tests.test_ingram_deck import long_model, probe  # noqa: F401

pytestmark = pytest.mark.skipif(not rc.available(), reason="needs LibreOffice and pdftotext")


@pytest.fixture(scope="module")
def baseline():
    return set(rc.problems(rc.words_by_page(ingram_template_bytes()), range(3, 16)))


def new_problems(model, baseline):
    return [p for p in rc.problems(rc.words_by_page(render("pptx", model)), range(3, 16)) if p not in baseline]


def real_like(m):
    m["text_answers"] = [dict(t, included=True, answer="\n".join(f"Item {i}: the assessment covers the on-premises estate and its readiness for the cloud, "
                                                                   f"including compute, databases and applications ({i * 7} of 171)." for i in range(20 if t["number"] == 1 else 12)))
                         if t["number"] in (1, 2) else t for t in m["text_answers"]]
    return m


def test_long_multi_line_answers_cause_no_visual_problems(probe, baseline):  # noqa: F811
    assert new_problems(real_like(probe), baseline) == []


def test_extreme_text_causes_no_visual_problems(probe, baseline):  # noqa: F811
    assert new_problems(real_like(long_model(probe)), baseline) == []


def test_ordinary_data_causes_no_visual_problems(probe, baseline):  # noqa: F811
    assert new_problems(probe, baseline) == []
