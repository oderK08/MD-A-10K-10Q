"""
Converts report HTML into a PDF using xhtml2pdf.

Chosen specifically because it's pure Python (reportlab underneath) --
no system-level dependency like Cairo/Pango/wkhtmltopdf, which may or
may not be installed in a given environment (a CI container, a user's
laptop, this project's own sandbox). That portability is worth the
tradeoff of more limited CSS support than a browser-based renderer would
give -- report.html_renderer's CSS is written to stay inside what
xhtml2pdf actually supports (basic box model, no flexbox/grid).

Installation note: on some systems, `pip install xhtml2pdf` fails with
"Cannot uninstall cryptography ..., RECORD file not found" if a
distro-managed `cryptography` package is already present without pip
metadata (a transitive dependency of xhtml2pdf's PDF-signing support
needs a newer cryptography than the OS package provides). If that
happens: `pip install --ignore-installed cryptography xhtml2pdf`.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Union

from xhtml2pdf import pisa

from .errors import PdfRenderError


def render_pdf(html: str) -> bytes:
    """Renders `html` to PDF bytes. Raises PdfRenderError on failure."""
    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer)
    if result.err:
        raise PdfRenderError(
            f"xhtml2pdf reported {result.err} error(s) while rendering the report."
        )
    return buffer.getvalue()


def save_pdf(html: str, output_path: Union[str, Path]) -> None:
    """Renders `html` to PDF and writes it to `output_path`."""
    Path(output_path).write_bytes(render_pdf(html))
