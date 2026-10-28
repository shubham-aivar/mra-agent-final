"""Read a filled-in MRA questionnaire (the 'Questions' sheet) into the canonical shape.

One agreed answer per question: a 1 to 5 rating, a Yes/No, or free text. Strict by design: a rating that is not a
whole number from 1 to 5 is an error with its row, never rounded or guessed. A blank rating is kept as blank (it is
shown as Incomplete and blocks release) and is never read as zero.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..loader import InputValidationError, build_response_set, read_json, sha256
from ..models import ResponseSet
from .evidence import load_evidence
from .tabular import find_column, read_table

_GUIDE_LEVEL = re.compile(r"^\s*([1-9])\s*[-\u2013\u2014:.]\s*\S")
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


def _derive_set_id(path: Path) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.stem).strip("-._") or "run"
    return s[:64]


def read_questionnaire(path: str | Path, cfg: dict, sheet: str | None = None) -> dict:
    qm = cfg["mapping"]["questionnaire"]
    headers, rows = read_table(path, sheet or qm.get("sheet"))
    cols = {k: find_column(headers, v) for k, v in qm["columns"].items()}
    errors = [f"no column found for '{k}' (looked for {qm['columns'][k]}; file has {headers})"
              for k in ("number", "type", "response", "comments") if cols[k] is None]
    if errors:
        raise InputValidationError(errors)
    yes, no = set(qm["yes_values"]), set(qm["no_values"])
    qmap = cfg["qmap"]
    by_number = {q.number: q for q in qmap.values()}
    ratings, answers, warnings = [], [], []
    seen: set[int] = set()
    lo, hi = cfg["rubric"]["scale_min"], cfg["rubric"]["scale_max"]
    for r in rows:
        where = f"row {r['_row']}"
        raw_n = r[cols["number"]].strip()
        try:
            n = int(raw_n)
        except ValueError:
            errors.append(f"{where}: '#' must be a whole number, got '{raw_n}'")
            continue
        q = by_number.get(n)
        if q is None:
            errors.append(f"{where}: question #{n} is not in the question bank (is this a newer questionnaire version?)")
            continue
        if n in seen:
            errors.append(f"{where}: question #{n} appears twice")
            continue
        seen.add(n)
        t = qm["type_names"].get(r[cols["type"]].strip().lower())
        if t != q.type:
            errors.append(f"{where}: question #{n} is '{r[cols['type']]}' in the file but '{q.type}' in the bank")
            continue
        if cols["question"] and _norm(r[cols["question"]]) != _norm(q.text):
            warnings.append(f"question #{n}: the wording differs from the question bank (version {cfg['versions']['question_bank']})")
        resp = r[cols["response"]].strip()
        comm = r[cols["comments"]].strip()
        if q.type == "rating":
            guide = r.get(cols["rating_guide"] or "", "").strip()
            m = _GUIDE_LEVEL.match(guide) if guide else None
            gl = int(m.group(1)) if m else None
            if not resp:
                if gl is not None:
                    warnings.append(f"question #{n}: no rating, but the Rating Guide shows level {gl}")
                continue
            if not re.fullmatch(r"\d+", resp):
                errors.append(f"{where}: rating for #{n} must be a whole number from {lo} to {hi}, got '{resp}'")
                continue
            score = int(resp)
            if not lo <= score <= hi:
                errors.append(f"{where}: rating for #{n} must be from {lo} to {hi}, got {score}")
                continue
            ratings.append({"question_id": q.id, "score": score, "guide_level": gl, "comment": comm})
        elif q.type == "yes_no":
            if not resp:
                continue
            low = resp.lower()
            if low in yes:
                answers.append({"question_id": q.id, "value": "Yes"})
            elif low in no:
                answers.append({"question_id": q.id, "value": "No"})
            else:
                errors.append(f"{where}: Yes/No answer for #{n} must be Yes or No, got '{resp}'")
        else:
            value = comm if qm["text_answer_from"] == "comments" and comm else resp
            if value:
                answers.append({"question_id": q.id, "value": value})
    missing = sorted(set(by_number) - seen)
    if missing and not errors:
        warnings.append(f"{len(missing)} question(s) of the bank are not in the file (first: #{missing[0]})")
    if errors:
        raise InputValidationError(errors)
    return {"ratings": ratings, "answers": answers, "warnings": warnings}


def load_input(path: str | Path, cfg: dict, evidence: str | Path | None = None, set_id: str | None = None,
               customer_label: str | None = None, meta: dict | None = None, sheet: str | None = None) -> tuple[ResponseSet, dict]:
    """Return (ResponseSet, input_hashes). A .json file is the canonical shape; .xlsx/.csv/.tsv is the questionnaire."""
    p = Path(path)
    hashes = {p.name: sha256(p.read_bytes())}
    if p.suffix.lower() == ".json":
        raw = read_json(p)
        raw.setdefault("evidence", [])
        raw.setdefault("answers", [])
        if evidence is not None:
            raw["evidence"] = list(raw["evidence"]) + load_evidence(evidence, cfg)
    else:
        raw = {"set_id": set_id or _derive_set_id(p), "customer_label": customer_label or p.stem,
               "evidence": load_evidence(evidence, cfg), **read_questionnaire(p, cfg, sheet)}
    if set_id:
        raw["set_id"] = set_id
    if customer_label:
        raw["customer_label"] = customer_label
    if meta:
        raw["meta"] = {**(raw.get("meta") or {}), **meta}
    if evidence is not None and Path(evidence).is_file():
        hashes[Path(evidence).name] = sha256(Path(evidence).read_bytes())
    return build_response_set(raw, cfg), hashes
