"""The model may describe the facts; the checker rejects anything that changes them."""
import json
import re

import pytest

from mra.explain.checker import check_activity_output, check_summary_output, id_regex
from mra.explain.explainer import Explainer, activity_facts, mask
from mra.pipeline import compute_result
from mra.ports import LlmReply

IDS = {"Q26", "Q27", "Q29", "Q33", "Q41", "BC-1", "BUS-01", "E1", "E2"}
LIM = {"observation_chars": 600, "next_step_chars": 300, "summary_chars": 700}
FORBID = [re.compile(r"\b(has|have|had) (now )?been (completed|implemented|fixed)\b", re.I)]
RX = id_regex(IDS)


def facts(colour="amber", score=3.3):
    return {"activity": "BC-1", "name": "High Level Business Case", "score": score, "colour": colour, "scale": [1, 5],
            "questions": [{"id": "Q26", "score": 4, "level_text": "4 - A business case exists", "next_level_text": None,
                           "comments": ["budget of 30 percent"], "evidence": [{"id": "E1", "text": "approved in March"}]},
                          {"id": "Q33", "score": 2, "level_text": "2 - Discovery underway", "next_level_text": "3 - Linked", "comments": [], "evidence": []}],
            "baseline_actions": ["Share the business case."]}


def good(**kw):
    return {"observation": "The business case exists at level 4 but the closure plan is at level 2.", "evidence_used": ["E1"],
            "next_step": "Share the business case with the wider team.", **kw}


def check(out, f=None):
    return check_activity_output(f or facts(), out, LIM, FORBID, RX)


def test_a_faithful_answer_passes():
    assert check(good()) == []


@pytest.mark.parametrize("field,text,needle", [
    ("observation", "The business case scores 3.4 overall.", "number 3.4"),
    ("observation", "This activity is red.", "says 'red'"),
    ("observation", "Q41 shows the same problem.", "'Q41'"),
    ("observation", "The plan has been completed already.", "claims an action was completed"),
    ("next_step", "x" * 301, "limit is 300"),
    ("observation", "", "missing or empty"),
])
def test_wrong_text_is_rejected_with_a_reason(field, text, needle):
    assert any(needle in r for r in check(good(**{field: text})))


def test_numbers_that_are_in_the_facts_or_their_texts_are_fine():
    assert check(good(observation="Level 4 and level 2 are named; the comment mentions 30 percent; the score is 3.3.")) == []


def test_amber_may_be_called_yellow_but_never_green_or_red():
    assert check(good(observation="This activity is amber.")) == []
    assert check(good(observation="This activity is yellow.")) == []
    assert check(good(observation="This activity is green."))
    assert check(good(observation="This activity is amber."), facts("green", 4.0))


def test_evidence_must_be_the_activitys_own():
    assert any("E2" in r for r in check(good(evidence_used=["E2"])))
    assert any("list of evidence ids" in r for r in check(good(evidence_used="E1")))
    assert check("not a dict") == ["item is not a JSON object"]


def test_the_summary_is_checked_the_same_way():
    f = {"perspectives": [{"name": "Business", "score": 3.0}]}
    ok = {"summary": "Business scores 3.0."}
    assert check_summary_output(f, ok, LIM, FORBID, RX) == []
    assert check_summary_output(f, {"summary": "Business scores 3.7."}, LIM, FORBID, RX)
    assert check_summary_output(f, {"summary": ""}, LIM, FORBID, RX)


class Fake:
    """complete() returns prepared replies in order; the last one repeats."""
    name, model_id = "fake", "fake-1"

    def __init__(self, replies):
        self.replies, self.n, self.prompts = list(replies), 0, []

    def complete(self, system, user, max_tokens):
        self.prompts.append(user)
        r = self.replies[min(self.n, len(self.replies) - 1)]
        self.n += 1
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, LlmReply) else LlmReply(r if isinstance(r, str) else json.dumps(r))

    def chat(self, *a):
        raise NotImplementedError


def explainer(llm, storage=None, **kw):
    from tests.conftest import FIXTURES
    prompts = json.loads((FIXTURES / "config" / "prompts.json").read_text())
    return Explainer(llm, storage, prompts, kw.get("batch", 4), kw.get("retries", 2), 3000, IDS, {"E1", "E2"}, kw.get("on_call"))


def item(i, **kw):
    f = facts(**kw)
    f["activity"] = i
    return f


def test_a_good_answer_is_labelled_model_and_nothing_is_wrong(storage):
    ex = explainer(Fake([[{"activity": "BC-1", **good()}]]), storage)
    out = ex.explain_activities([facts()])["BC-1"]
    assert out["observation"]["source"] == "model" and out["attempts"] == 1 and ex.stats["fallbacks"] == 0


def test_a_rejected_item_is_retried_with_the_reasons_then_accepted():
    bad = [{"activity": "BC-1", **good(observation="It scores 9.9.")}]
    llm = Fake([bad, [{"activity": "BC-1", **good()}]])
    out = explainer(llm).explain_activities([facts()])["BC-1"]
    assert out["attempts"] == 2 and "rejected for these reasons" in llm.prompts[1] and "9.9" in llm.prompts[1]


def test_after_the_retries_a_labelled_fallback_with_the_baseline_action():
    llm = Fake([[{"activity": "BC-1", **good(observation="Always 9.9.")}]])
    ex = explainer(llm, retries=1)
    out = ex.explain_activities([facts()])["BC-1"]
    assert out["observation"]["source"] == "fallback" and out["observation"]["text"].startswith("FALLBACK TEXT")
    assert out["next_step"] == {"text": "Share the business case.", "source": "library"} and ex.stats["fallbacks"] == 1 and llm.n == 2


def test_each_item_is_judged_alone_one_bad_item_does_not_spoil_the_batch():
    good_item, bad_item = {"activity": "BC-1", **good()}, {"activity": "BC-2", **good(observation="red!")}
    llm = Fake([[good_item, bad_item], [{"activity": "BC-2", **good()}]])
    out = explainer(llm).explain_activities([item("BC-1"), item("BC-2")])
    assert out["BC-1"]["attempts"] == 1 and out["BC-2"]["attempts"] == 2


def test_fences_invalid_json_and_missing_items_are_handled():
    fenced = "```json\n" + json.dumps([{"activity": "BC-1", **good()}]) + "\n```"
    assert explainer(Fake([fenced])).explain_activities([facts()])["BC-1"]["observation"]["source"] == "model"
    assert explainer(Fake(["not json", "still not"]), retries=1).explain_activities([facts()])["BC-1"]["observation"]["source"] == "fallback"
    assert explainer(Fake([[]]), retries=0).explain_activities([facts()])["BC-1"]["observation"]["source"] == "fallback"


def test_a_cut_off_reply_makes_the_next_attempt_one_item_at_a_time():
    cut = LlmReply("[{", stop_reason="max_tokens")
    llm = Fake([cut, [{"activity": "BC-1", **good()}], [{"activity": "BC-2", **good()}]])
    out = explainer(llm).explain_activities([item("BC-1"), item("BC-2")])
    assert {k: v["observation"]["source"] for k, v in out.items()} == {"BC-1": "model", "BC-2": "model"}
    assert llm.prompts[1].count('"activity": "BC') == 1


def test_a_model_failure_never_crashes_a_run():
    ex = explainer(Fake([RuntimeError("throttled")]), retries=1)
    assert ex.explain_activities([facts()])["BC-1"]["observation"]["source"] == "fallback" and ex.stats["errors"] == 2


def test_only_checked_text_is_cached_and_a_rerun_makes_no_calls(storage):
    llm = Fake([[{"activity": "BC-1", **good()}]])
    explainer(llm, storage).explain_activities([facts()])
    again = Fake([RuntimeError("must not be called")])
    ex = explainer(again, storage)
    assert ex.explain_activities([facts()])["BC-1"]["observation"]["source"] == "model" and again.n == 0 and ex.stats["cache_hits"] == 1
    bad = Fake([[{"activity": "BC-9", **good(observation="9.9")}]])
    explainer(bad, storage, retries=0).explain_activities([item("BC-9")])
    assert not any("BC-9" in storage.get(k).decode() for k in storage.list("cache/explain/") if "BC-9" in storage.get(k).decode())


def test_every_rejection_is_logged_with_its_reason():
    events = []
    explainer(Fake([[{"activity": "BC-1", **good(observation="It scores 9.9.")}], [{"activity": "BC-1", **good()}]]),
              on_call=events.append).explain_activities([facts()])
    rej = [e for e in events if e.get("outcome") == "item_rejected"]
    assert len(rej) == 1 and rej[0]["activity"] == "BC-1" and "9.9" in rej[0]["reasons"][0]


def test_the_summary_falls_back_to_a_labelled_plain_sentence():
    f = {"scale": [1, 5], "perspectives": [{"name": "Business", "score": 3.0}, {"name": "People", "score": None}], "workstreams": [],
         "incomplete": ["People"], "validation_item_count": 4}
    out = explainer(Fake([{"summary": "It is 9.9."}]), retries=0).explain_summary(f)
    assert out["source"] == "fallback" and "Business 3.0" in out["text"] and "People" not in out["text"]
    assert explainer(Fake([{"summary": "Business scores 3.0 of 5."}])).explain_summary(f)["source"] == "model"


def test_emails_are_masked_before_a_model_sees_them(cfg, golden, rset):
    assert mask("write to jane.doe@example.com now", True) == "write to [email] now" and mask("a@b.co", False) == "a@b.co"
    res = compute_result(rset(golden["ratings"], evidence=[{"evidence_id": "E1", "text": "mail jane.doe@example.com", "applies_to": ["Q26"]}]), cfg)
    qby = {q["question_id"]: q for q in res["questions"]}
    act = next(a for a in res["activities"] if a["id"] == "BC-1")
    act["needs_action"] = True
    f = activity_facts(act, qby, {"E1": "mail jane.doe@example.com"}, cfg["rubric"], True, {"Q26": ["ask joe@corp.com"]})
    assert "example.com" not in json.dumps(f) and "corp.com" not in json.dumps(f) and "[email]" in json.dumps(f)
