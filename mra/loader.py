"""Load and validate configuration and response sets.

Validation collects every problem and reports them together, so a person can fix the whole
input in one pass. Nothing is silently dropped or corrected.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .models import Answer, Evidence, Question, Rating, ResponseSet

SET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
QUESTION_TYPES = ("rating", "yes_no", "text")
META_FIELDS = ("facilitator", "presenter", "workshop_date", "readout_date", "internal_contact", "sponsor_contact")
CONFIG_FILES = ("question_bank", "rubric", "workstreams", "action_library", "input_mapping", "prompts")


class InputValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def read_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_rubric(r: dict, problems: list[str]) -> None:
    for k in ("scale_min", "scale_max"):
        if not isinstance(r.get(k), int):
            problems.append(f"rubric: '{k}' must be a whole number")
    if isinstance(r.get("scale_min"), int) and isinstance(r.get("scale_max"), int) and r["scale_min"] >= r["scale_max"]:
        problems.append("rubric: scale_min must be below scale_max")
    c = r.get("colours", {})
    if not _num(c.get("green_from")):
        problems.append("rubric: colours.green_from must be a number")
    rb = c.get("red_below")
    if rb is not None and not _num(rb):
        problems.append("rubric: colours.red_below must be a number or null")
    if _num(rb) and _num(c.get("green_from")) and not rb < c["green_from"]:
        problems.append("rubric: colours.red_below must be below colours.green_from")
    for k in ("display_decimals", "internal_decimals", "radar_decimals"):
        if not isinstance(r.get("rounding", {}).get(k), int) or r["rounding"][k] < 0:
            problems.append(f"rubric: rounding.{k} must be a whole number of 0 or more")
    if not _num(r.get("rounding", {}).get("radar_multiplier")) or r["rounding"]["radar_multiplier"] <= 0:
        problems.append("rubric: rounding.radar_multiplier must be a positive number")
    for k in ("radar_guide", "action_below", "high_claim_at_or_above"):
        if not _num(r.get(k)):
            problems.append(f"rubric: '{k}' must be a number")
    pr = r.get("presentation", {})
    for k in ("strengths_n", "gaps_n", "key_strong_n", "key_weak_n"):
        if not isinstance(pr.get(k), int) or pr[k] < 0:
            problems.append(f"rubric: presentation.{k} must be a whole number of 0 or more")
    if not isinstance(pr.get("key_observation_pages"), list):
        problems.append("rubric: presentation.key_observation_pages must be a list of workstream-id lists")


def load_config(config_dir: str | Path) -> dict:
    d = Path(config_dir)
    files = {n: read_json(d / f"{n}.json") for n in CONFIG_FILES}
    bank, rubric, wsfile, lib = files["question_bank"], files["rubric"], files["workstreams"], files["action_library"]
    problems: list[str] = []
    _check_rubric(rubric, problems)

    perspectives = bank.get("perspectives", [])
    pids = [p["id"] for p in perspectives]
    areas = list(bank.get("areas", []))
    area_ids = [a["id"] for a in areas]
    if len(set(pids)) != len(pids):
        problems.append("question bank has duplicate perspective ids")
    if len(set(area_ids)) != len(area_ids):
        problems.append("question bank has duplicate area ids")
    for a in areas:
        if a.get("perspective") is not None and a["perspective"] not in pids:
            problems.append(f"area {a['id']}: unknown perspective '{a['perspective']}'")
    modules = list(bank.get("modules", []))
    module_ids = {m["id"] for m in modules}
    questions: list[Question] = []
    seen: set[str] = set()
    for q in bank.get("questions", []):
        try:
            qq = Question(id=q["id"], number=int(q["number"]), section=q["section"], category=q["category"], area=q["area"],
                          perspective=q.get("perspective"), type=q["type"], text=q["text"], levels=tuple(q.get("levels", ())),
                          module=q.get("module"))
        except (KeyError, TypeError, ValueError) as e:
            problems.append(f"question {q.get('id', '?')}: missing or bad field ({e})")
            continue
        if qq.id in seen:
            problems.append(f"duplicate question id '{qq.id}'")
        seen.add(qq.id)
        if qq.type not in QUESTION_TYPES:
            problems.append(f"question {qq.id}: type must be one of {QUESTION_TYPES}")
        if qq.area not in area_ids:
            problems.append(f"question {qq.id}: unknown area '{qq.area}'")
        if qq.perspective is not None and qq.perspective not in pids:
            problems.append(f"question {qq.id}: unknown perspective '{qq.perspective}'")
        if qq.module is not None and qq.module not in module_ids:
            problems.append(f"question {qq.id}: unknown module '{qq.module}'")
        if qq.type == "rating" and len(qq.levels) != 5:
            problems.append(f"question {qq.id}: a rating question needs exactly 5 level descriptions, has {len(qq.levels)}")
        questions.append(qq)
    for m in modules:
        if m.get("gate") not in seen:
            problems.append(f"module {m['id']}: gate question '{m.get('gate')}' does not exist")

    qmap = {q.id: q for q in questions}
    core = [q for q in questions if q.type == "rating" and q.perspective is not None]
    mapped: dict[str, str] = {}
    workstreams, activities = [], []
    act_ids: set[str] = set()
    for w in wsfile.get("workstreams", []):
        workstreams.append({"id": w["id"], "name": w["name"], "activities": [a["id"] for a in w["activities"]]})
        for a in w["activities"]:
            if a["id"] in act_ids:
                problems.append(f"duplicate activity id '{a['id']}'")
            act_ids.add(a["id"])
            if a.get("phase") not in ("Pre-Mobilize", "Mobilize"):
                problems.append(f"activity {a['id']}: phase must be 'Pre-Mobilize' or 'Mobilize'")
            for qid in a["questions"]:
                if qid not in qmap:
                    problems.append(f"activity {a['id']}: unknown question '{qid}'")
                elif qmap[qid].type != "rating" or qmap[qid].perspective is None:
                    problems.append(f"activity {a['id']}: question {qid} is not one of the scored ratings")
                elif qid in mapped:
                    problems.append(f"question {qid} is mapped to two activities ({mapped[qid]} and {a['id']})")
                else:
                    mapped[qid] = a["id"]
            activities.append({"id": a["id"], "name": a["name"], "phase": a["phase"], "workstream": w["id"],
                               "workstream_name": w["name"], "questions": list(a["questions"])})
    for q in core:
        if q.id not in mapped:
            problems.append(f"scored question {q.id} is not mapped to any activity (config/workstreams.json)")
    ws_ids = {w["id"] for w in workstreams}
    for page in rubric.get("presentation", {}).get("key_observation_pages", []):
        for wid in page:
            if wid not in ws_ids:
                problems.append(f"rubric: key_observation_pages names unknown workstream '{wid}'")
    for al in lib.get("actions", []):
        for aid in al.get("activities", []):
            if aid not in act_ids:
                problems.append(f"action library {al['id']}: unknown activity '{aid}'")
    if problems:
        raise InputValidationError(problems)

    return {
        "questions": tuple(questions), "qmap": qmap, "areas": tuple(areas), "perspectives": tuple(perspectives),
        "modules": tuple(modules), "workstreams": tuple(workstreams), "activities": tuple(activities),
        "activity_of": mapped, "rubric": rubric, "actions": lib, "mapping": files["input_mapping"], "prompts": files["prompts"],
        "versions": {k: v.get("version", "n/a") for k, v in files.items()},
        "config_hash": sha256(canonical(files))[:16],
        "placeholder_notes": [f"{k}: {v['_status']}" for k, v in files.items()
                              if "_status" in v and re.split(r"[.\s]", v["_status"].strip(), maxsplit=1)[0].upper()
                              in ("PLACEHOLDER", "PROPOSED", "INFERRED")],
    }


def build_response_set(raw: dict, cfg: dict) -> ResponseSet:
    """Validate a raw dict (from any reader) and return a ResponseSet. Reports all problems at once."""
    errors: list[str] = []
    qmap = cfg["qmap"]
    lo, hi = cfg["rubric"]["scale_min"], cfg["rubric"]["scale_max"]
    for key in ("set_id", "customer_label", "ratings", "answers", "evidence"):
        if key not in raw:
            errors.append(f"missing top-level field '{key}'")
    if errors:
        raise InputValidationError(errors)
    if not SET_ID.match(str(raw["set_id"])):
        raise InputValidationError([f"set_id '{raw['set_id']}' must be 1-64 letters, digits, '.', '_' or '-' "
                                    "and start with a letter or digit (it becomes part of a storage key)"])
    meta_in = raw.get("meta") or {}
    bad_meta = sorted(set(meta_in) - set(META_FIELDS))
    if bad_meta:
        errors.append(f"unknown meta field(s) {bad_meta}; allowed: {list(META_FIELDS)}")
    meta = {k: str(v).strip() for k, v in meta_in.items() if k in META_FIELDS and v not in (None, "")}

    evidence: list[Evidence] = []
    seen_e: set[str] = set()
    targets = set(qmap) | {a["id"] for a in cfg["areas"]} | {a["id"] for a in cfg["activities"]}
    for i, e in enumerate(raw["evidence"]):
        eid = e.get("evidence_id")
        if not eid or not str(e.get("text", "")).strip():
            errors.append(f"evidence[{i}]: needs evidence_id and text")
            continue
        if eid in seen_e:
            errors.append(f"evidence[{i}]: duplicate evidence_id '{eid}'")
        seen_e.add(eid)
        applies = tuple(e.get("applies_to", []))
        for t in applies:
            if t not in targets:
                errors.append(f"evidence {eid}: applies_to unknown question, category or activity '{t}'")
        evidence.append(Evidence(eid, e.get("source", ""), e.get("kind", "interview_note"), e["text"], applies))

    ratings: list[Rating] = []
    seen_q: set[str] = set()
    for i, r in enumerate(raw["ratings"]):
        qid = r.get("question_id")
        where = f"rating[{i}] ({qid})"
        q = qmap.get(qid)
        if q is None:
            errors.append(f"{where}: unknown question_id")
            continue
        if q.type != "rating":
            errors.append(f"{where}: {qid} is a {q.type} question, not a rating")
            continue
        if qid in seen_q:
            errors.append(f"{where}: a second rating for the same question (one agreed rating per question)")
            continue
        seen_q.add(qid)
        score = r.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not lo <= score <= hi:
            errors.append(f"{where}: rating must be a whole number from {lo} to {hi}, got {score!r}")
            continue
        gl = r.get("guide_level")
        if gl is not None and (isinstance(gl, bool) or not isinstance(gl, int) or not lo <= gl <= hi):
            errors.append(f"{where}: guide_level must be a whole number from {lo} to {hi} or null")
            continue
        ratings.append(Rating(qid, score, gl, str(r.get("comment", "") or "")))

    answers: list[Answer] = []
    seen_a: set[str] = set()
    for i, a in enumerate(raw["answers"]):
        qid = a.get("question_id")
        q = qmap.get(qid)
        if q is None:
            errors.append(f"answer[{i}] ({qid}): unknown question_id")
        elif q.type == "rating":
            errors.append(f"answer[{i}] ({qid}): a rating question is answered in 'ratings'")
        elif qid in seen_a:
            errors.append(f"answer[{i}] ({qid}): a second answer for the same question")
        else:
            seen_a.add(qid)
            answers.append(Answer(qid, str(a.get("value", "") or "")))
    if not ratings and not errors:
        errors.append("the questionnaire has no ratings at all")
    if errors:
        raise InputValidationError(errors)
    return ResponseSet(raw["set_id"], raw["customer_label"], tuple(ratings), tuple(answers), tuple(evidence), meta,
                       tuple(raw.get("warnings") or ()))


def load_response_set(path: str | Path, cfg: dict) -> ResponseSet:
    return build_response_set(read_json(path), cfg)
