"""Which deck is filled, and how. With no template the Ingram MAP deliverable is used; any deck recognised as the Ingram format gets the Ingram
treatment, and anything else is treated as the AWS MRA readout format."""
from __future__ import annotations

from .ingram_deck import ingram_template_bytes, is_ingram, render_ingram
from .pptx_out import render_pptx


def render_deck(m: dict, template: bytes | None = None) -> bytes:
    data = template if template is not None else ingram_template_bytes()
    return render_ingram(m, data) if is_ingram(data) else render_pptx(m, data)
