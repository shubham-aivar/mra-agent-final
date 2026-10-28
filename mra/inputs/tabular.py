"""Read a CSV, TSV or XLSX file into a list of dict rows with one uniform shape."""
from __future__ import annotations

import csv
from pathlib import Path

from ..loader import InputValidationError


def _clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_table(path: str | Path, sheet: str | None = None) -> tuple[list[str], list[dict]]:
    """Return (headers, rows). Each row has its cells as strings plus '_row' (the 1-based row number)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".csv", ".tsv"):
        with open(p, newline="", encoding="utf-8-sig") as fh:
            raw = list(csv.reader(fh, delimiter="\t" if ext == ".tsv" else ","))
    elif ext in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
        except ImportError as e:  # pragma: no cover
            raise InputValidationError(["reading .xlsx needs openpyxl (pip install openpyxl)"]) from e
        wb = load_workbook(p, read_only=True, data_only=True)
        if sheet and sheet not in wb.sheetnames:
            raise InputValidationError([f"sheet '{sheet}' not found; sheets are {wb.sheetnames}"])
        raw = [list(r) for r in (wb[sheet] if sheet else wb[wb.sheetnames[0]]).iter_rows(values_only=True)]
        wb.close()
    else:
        raise InputValidationError([f"unsupported file type '{ext}' (use .csv, .tsv or .xlsx)"])

    raw = [[_clean(c) for c in row] for row in raw]
    start = next((i for i, r in enumerate(raw) if any(r)), None)
    if start is None:
        raise InputValidationError([f"{p.name}: file is empty"])
    headers = raw[start]
    named = [h for h in headers if h]
    dupes = sorted({h for h in named if named.count(h) > 1})
    if dupes:
        raise InputValidationError([f"{p.name}: duplicate column headers {dupes}"])
    rows = []
    for i, r in enumerate(raw[start + 1:], start=start + 2):
        if not any(r):
            continue
        row = {h: (r[j] if j < len(r) else "") for j, h in enumerate(headers) if h}
        row["_row"] = i
        rows.append(row)
    return named, rows


def find_column(headers: list[str], aliases: list[str]) -> str | None:
    """First header that matches any alias, ignoring case and surrounding spaces."""
    wanted = [a.strip().lower() for a in aliases]
    for a in wanted:
        for h in headers:
            if h.strip().lower() == a:
                return h
    return None
