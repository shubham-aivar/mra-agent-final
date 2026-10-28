import itertools
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from mra.adapters.llm_stub import StubLlm
from mra.adapters.local_storage import LocalStorage
from mra.loader import build_response_set, load_config
from mra.service import AssessmentService
from mra.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"      # a frozen copy of the config and a golden engagement, so tests guard the code, not the content
Q_HEADER = ["Section", "Category", "#", "Question", "Response Type", "Response", "Rating Guide (if applicable)", "Comments",
            "Rating 1", "Rating 2", "Rating 3", "Rating 4", "Rating 5"]


@pytest.fixture(scope="session")
def cfg():
    return load_config(FIXTURES / "config")


@pytest.fixture(scope="session")
def golden():
    return json.loads((FIXTURES / "golden" / "engagement_a.json").read_text())


def make_raw(cfg, ratings, answers=None, evidence=None, meta=None, set_id="t", customer="Customer A"):
    """The canonical input shape. ratings: {question_id: score}; answers: {question_id: text}."""
    return {"set_id": set_id, "customer_label": customer, "ratings": [{"question_id": q, "score": s} for q, s in ratings.items()],
            "answers": [{"question_id": q, "value": v} for q, v in (answers or {}).items()], "evidence": evidence or [], "meta": meta or {}}


@pytest.fixture
def rset(cfg):
    def _make(ratings, answers=None, evidence=None, meta=None, set_id="t"):
        return build_response_set(make_raw(cfg, ratings, answers, evidence, meta, set_id), cfg)
    return _make


def core_ids(cfg):
    return [q.id for q in cfg["questions"] if q.type == "rating" and q.perspective]


@pytest.fixture
def all3(cfg):
    """Every one of the 62 scored questions rated 3."""
    return {q: 3 for q in core_ids(cfg)}


@pytest.fixture
def full_ratings(golden):
    """The golden engagement with its one blank (Q42) rated 1."""
    return {**golden["ratings"], "Q42": 1}


def write_questionnaire(path, cfg, ratings, answers=None, guide=True, overrides=None):
    """A filled-in questionnaire workbook in the AWS layout. answers: {number: text}. overrides: {number: {column: value}}."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Questions"
    ws.append(Q_HEADER)
    for q in sorted(cfg["questions"], key=lambda x: x.number):
        lv = list(q.levels) + [None] * (5 - len(q.levels))
        resp = comm = guide_txt = None
        if q.type == "rating" and q.id in ratings:
            resp = ratings[q.id]
            guide_txt = q.levels[resp - 1] if guide else None
        elif q.type == "yes_no":
            resp = (answers or {}).get(q.number)
        elif q.type == "text":
            comm = (answers or {}).get(q.number)
        row = [q.section, q.category, q.number, q.text, {"rating": "Rating", "yes_no": "Yes/No", "text": "Text"}[q.type], resp, guide_txt, comm, *lv]
        for col, val in ((overrides or {}).get(q.number) or {}).items():
            row[Q_HEADER.index(col)] = val
        ws.append(row)
    wb.save(path)
    return path


def fixed_clock():
    c = itertools.count(1)

    def clock() -> str:
        n = next(c)
        return f"2026-10-15T10:{n // 60:02d}:{n % 60:02d}+00:00"
    return clock


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "store")


@pytest.fixture
def svc(storage, cfg):
    return AssessmentService(storage, StubLlm(), cfg, Settings(), clock=fixed_clock())
