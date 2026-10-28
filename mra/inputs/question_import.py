"""Turn the AWS MRA questionnaire (the 'Questions' sheet, with or without answers) into config/question_bank.json.

Only template content is read: section, category, number, question text, response type and the five rating-level
descriptions. Any answers in the file are ignored, so a filled-in customer export can be used as the source without
copying customer content into the configuration. Column names, the six scored sections, the optional modules and
their gate questions all live in config/input_mapping.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..loader import InputValidationError, canonical, sha256
from .tabular import find_column, read_table


def _column_map(headers: list[str], mapping: dict) -> tuple[dict, list[str]]:
    cols = {k: find_column(headers, v) for k, v in mapping["questionnaire"]["columns"].items()}
    errors = [f"no column found for '{k}' (looked for {mapping['questionnaire']['columns'][k]}; file has {headers})"
              for k, v in cols.items() if v is None and k not in ("response", "rating_guide", "comments")]
    return cols, errors


def import_questions(path: str | Path, mapping: dict, version: str, sheet: str | None = None) -> tuple[dict, dict]:
    qm, ti = mapping["questionnaire"], mapping["template_import"]
    headers, rows = read_table(path, sheet or qm.get("sheet"))
    cols, errors = _column_map(headers, mapping)
    if errors:
        raise InputValidationError(errors)
    core = {p["section"]: p for p in ti["perspectives"]}
    mods = {m["section"]: m for m in ti["modules"]}
    other = ti["other_sections"]
    types = qm["type_names"]
    area_ids: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    areas, questions = [], []
    seen_numbers: set[int] = set()
    for r in rows:
        where = f"row {r['_row']}"
        section, cat, text = r[cols["section"]].strip(), r[cols["category"]].strip(), r[cols["question"]].strip()
        raw_n, raw_t = r[cols["number"]].strip(), r[cols["type"]].strip().lower()
        if section in core:
            prefix, persp, module = core[section]["prefix"], core[section]["id"], None
        elif section in mods:
            prefix, persp, module = mods[section]["prefix"], None, mods[section]["id"]
        elif section in other:
            prefix, persp, module = other[section], None, None
        else:
            errors.append(f"{where}: unknown section '{section}'")
            continue
        try:
            number = int(raw_n)
        except ValueError:
            errors.append(f"{where}: '#' must be a whole number, got '{raw_n}'")
            continue
        if number in seen_numbers:
            errors.append(f"{where}: duplicate question number {number}")
            continue
        seen_numbers.add(number)
        qtype = types.get(raw_t)
        if qtype is None:
            errors.append(f"{where}: unknown response type '{r[cols['type']]}' (known: {sorted(types)})")
            continue
        if not text or not cat:
            errors.append(f"{where}: category and question text must not be empty")
            continue
        levels = [r.get(cols[f"level{i}"], "").strip() for i in range(1, 6)]
        if qtype == "rating" and not all(levels):
            errors.append(f"{where}: rating question {number} needs all five rating-level descriptions")
            continue
        key = (section, cat)
        if key not in area_ids:
            counters[prefix] = counters.get(prefix, 0) + 1
            aid = f"{prefix}-{counters[prefix]:02d}"
            area_ids[key] = aid
            areas.append({"id": aid, "perspective": persp, "section": section, "name": cat, "module": module})
        questions.append({"id": f"Q{number}", "number": number, "section": section, "category": cat, "area": area_ids[key],
                          "perspective": persp, "type": qtype, "text": text, "levels": levels if qtype == "rating" else [],
                          "module": module})
    if errors:
        raise InputValidationError(errors)
    if not questions:
        raise InputValidationError([f"{Path(path).name}: no questions found"])
    by_number = {q["number"]: q["id"] for q in questions}
    modules, warnings = [], []
    for m in ti["modules"]:
        gate = by_number.get(m["gate_number"])
        if gate is None:
            warnings.append(f"module {m['id']}: gate question #{m['gate_number']} not found in this file")
            continue
        modules.append({"id": m["id"], "name": m["name"], "section": m["section"], "gate": gate})
    questions.sort(key=lambda q: q["number"])
    src = Path(path)
    bank = {"_status": f"IMPORTED from {src.name} (sha256 {sha256(src.read_bytes())[:12]}). Template content only; answers ignored.",
            "version": version,
            "perspectives": [{"id": p["id"], "name": p["section"]} for p in ti["perspectives"]],
            "areas": areas, "modules": modules, "questions": questions}
    scored = [q for q in questions if q["perspective"]]
    report = {"source": src.name, "questions": len(questions), "areas": len(areas),
              "scored_ratings": sum(q["type"] == "rating" for q in scored),
              "by_type": {t: sum(q["type"] == t for q in questions) for t in ("rating", "yes_no", "text")},
              "per_perspective": {p["id"]: sum(q["perspective"] == p["id"] for q in questions) for p in ti["perspectives"]},
              "modules": [m["id"] for m in modules], "warnings": warnings, "bank_hash": sha256(canonical(bank))[:12]}
    return bank, report


def write_bank(bank: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
