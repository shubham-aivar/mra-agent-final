"""Interview notes and other evidence: a folder of .txt/.md files, a table, or a JSON list."""
from __future__ import annotations

from pathlib import Path

from ..loader import InputValidationError, read_json
from .tabular import find_column, read_table

MAX_NOTE_BYTES = 200_000


def _split(v: str, sep: str) -> list[str]:
    return [x.strip() for x in v.split(sep) if x.strip()]


def load_evidence(path: str | Path | None, cfg: dict) -> list[dict]:
    if path is None:
        return []
    p = Path(path)
    sep = cfg["mapping"]["multi_value_separator"]
    if p.is_dir():
        out, errors = [], []
        for f in sorted(x for x in p.iterdir() if x.suffix.lower() in (".txt", ".md")):
            if f.stat().st_size > MAX_NOTE_BYTES:
                errors.append(f"{f.name}: note is larger than {MAX_NOTE_BYTES} bytes")
                continue
            lines = f.read_text(encoding="utf-8").splitlines()
            applies: list[str] = []
            if lines and lines[0].lower().startswith("applies_to:"):
                applies, lines = _split(lines[0].split(":", 1)[1], ","), lines[1:]
            out.append({"evidence_id": f"E-{f.stem}", "source": f.name, "kind": "interview_note",
                        "text": "\n".join(lines).strip(), "applies_to": applies})
        if errors:
            raise InputValidationError(errors)
        return out
    if p.suffix.lower() == ".json":
        raw = read_json(p)
        return raw["evidence"] if isinstance(raw, dict) else raw
    headers, rows = read_table(p)
    ec = {k: find_column(headers, v) for k, v in cfg["mapping"]["evidence_columns"].items()}
    if ec["evidence_id"] is None or ec["text"] is None:
        raise InputValidationError([f"evidence table needs an id and a text column; file has {headers}"])
    return [{"evidence_id": r[ec["evidence_id"]], "source": r.get(ec["source"] or "", ""),
             "kind": r.get(ec["kind"] or "", "") or "interview_note", "text": r[ec["text"]],
             "applies_to": _split(r.get(ec["applies_to"] or "", ""), sep)} for r in rows if r[ec["evidence_id"]]]
