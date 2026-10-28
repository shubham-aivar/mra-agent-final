"""Renderers turn a report model into file bytes. To add a format, register it here; nothing else changes.
Writers are imported only when used, so scoring runs need no Excel, Word or PowerPoint libraries."""
from __future__ import annotations

from importlib import import_module

# kind -> (module, function, file extension, text added to the file name, needs a template). The deck falls back to the built-in default.
RENDERERS = {"xlsx": ("mra.outputs.xlsx_out", "render_xlsx", "xlsx", "", False),
             "docx": ("mra.outputs.docx_out", "render_docx", "docx", "", False),
             "pptx": ("mra.outputs.deck", "render_deck", "pptx", "", False),
             "todo": ("mra.outputs.todo_out", "render_todo", "md", "_open_items", False),
             "context": ("mra.outputs.context_out", "render_context", "md", "_planning_context", False)}


def filename(set_id: str, status: str, kind: str) -> str:
    if kind not in RENDERERS:
        raise ValueError(f"unknown export '{kind}' (available: {sorted(RENDERERS)})")
    return f"MRA_{set_id}_{status}{RENDERERS[kind][3]}.{RENDERERS[kind][2]}"


def render(kind: str, model: dict, template: bytes | None = None) -> bytes:
    if kind not in RENDERERS:
        raise ValueError(f"unknown export '{kind}' (available: {sorted(RENDERERS)})")
    module, fn, _, _, needs = RENDERERS[kind]
    if needs and template is None:
        raise ValueError(f"the {kind} output is filled into the AWS template: pass it with --{kind}-template FILE")
    try:
        writer = getattr(import_module(module), fn)
    except ImportError as e:  # pragma: no cover
        raise ValueError(f"export '{kind}' needs extra packages: pip install -r requirements.txt ({e})") from e
    return writer(model, template=template)
