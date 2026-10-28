"""Plain data types. No AWS or model code lives here."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Question:
    id: str                      # "Q23": the AWS question number is the stable id (reports and decks cite it)
    number: int
    section: str                 # as in the questionnaire: Scope, Activities, Business, ..., "Optional - SAP"
    category: str
    area: str                    # category id, for example BUS-01
    perspective: str | None      # business, platform, people, governance, operations, security; None outside the six
    type: str                    # rating, yes_no or text
    text: str
    levels: tuple[str, ...] = ()  # the five rating-level descriptions (rating questions only)
    module: str | None = None    # optional module id, or None


@dataclass(frozen=True)
class Rating:
    """The one agreed rating for a question (a workshop produces one answer, not one per person)."""
    question_id: str
    score: int
    guide_level: int | None = None   # the level the questionnaire's own 'Rating Guide' text points to, if it shows one
    comment: str = ""


@dataclass(frozen=True)
class Answer:
    """A Yes/No or free-text answer."""
    question_id: str
    value: str


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source: str
    kind: str
    text: str
    applies_to: tuple[str, ...] = ()   # question, category or activity ids this evidence supports


@dataclass(frozen=True)
class ResponseSet:
    set_id: str
    customer_label: str
    ratings: tuple[Rating, ...]
    answers: tuple[Answer, ...]
    evidence: tuple[Evidence, ...]
    meta: dict = field(default_factory=dict)   # report details: facilitator, workshop_date, readout_date, ...
    warnings: tuple[str, ...] = ()             # reader notes that are not errors (for example a question wording that differs)
