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
import re
from pathlib import Path
from typing import Union

from xhtml2pdf import pisa

from .errors import PdfRenderError

# Counts page objects in the produced PDF. `/Type /Page` also prefixes
# `/Type /Pages` (the page-tree node), hence the negative lookahead --
# without it every document counts one page too many. Cross-checked
# against `pdfinfo` on real multi-page renders.
_PAGE_OBJECT_RE = re.compile(rb"/Type\s*/Page(?!s)")

# Progressive compaction steps, applied only when a document overruns
# its page budget. Each step appends an override stylesheet (later rules
# win in CSS), shrinking type and spacing a little more. Deliberately
# small increments: the first step that fits is used, so a report that
# only just overflows is barely altered.
_COMPACTION_STEPS = [
    "body { font-size: 9pt; line-height: 1.28; }"
    " th, td { padding: 1.7pt 4.5pt; font-size: 8pt; }"
    " h2 { margin: 9pt 0 3pt 0; }",
    "body { font-size: 8.5pt; line-height: 1.22; }"
    " th, td { padding: 1.4pt 4pt; font-size: 7.5pt; }"
    " h2 { margin: 7pt 0 3pt 0; font-size: 10pt; }"
    " p { margin: 0 0 3.5pt 0; }",
    "body { font-size: 8pt; line-height: 1.18; }"
    " th, td { padding: 1.1pt 3.5pt; font-size: 7pt; }"
    " h2 { margin: 6pt 0 2pt 0; font-size: 9.5pt; }"
    " p { margin: 0 0 3pt 0; }"
    " .muted, .note { font-size: 7pt; }",
]


def page_count(pdf_bytes: bytes) -> int:
    """Number of pages in an already-rendered PDF."""
    return len(_PAGE_OBJECT_RE.findall(pdf_bytes))


def render_pdf(html: str) -> bytes:
    """Renders `html` to PDF bytes. Raises PdfRenderError on failure."""
    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer)
    if result.err:
        raise PdfRenderError(
            f"xhtml2pdf reported {result.err} error(s) while rendering the report."
        )
    return buffer.getvalue()


def _with_override_css(html: str, css: str) -> str:
    """
    Appends an override stylesheet just before </head>, so its rules win
    over the document's own without editing them.
    """
    marker = "</head>"
    index = html.find(marker)
    if index == -1:  # no head to extend -- render unchanged rather than corrupt the doc
        return html
    return f"{html[:index]}<style>{css}</style>{html[index:]}"


def render_pdf_fitted(html: str, max_pages: int) -> bytes:
    """
    Renders `html`, compacting it as needed so the result fits in
    `max_pages`.

    The report has a page budget and content length is not knowable in
    advance -- a Q&A that dodged eight questions produces four times the
    rows of one that dodged two. Rather than tuning the stylesheet
    against one sample and hoping, this renders, counts the real pages,
    and retries with progressively tighter type until it fits.

    Returns the first rendering that fits. If even the tightest step
    overruns, returns THAT rendering rather than raising: a slightly
    long report is far more useful to the reader than no report, and the
    caller can compare `page_count()` against its budget if it needs to
    know. Never silently drops content.

    Measured range: the steps below recover ONE page and not two. A
    document that naturally needs three comes back to two, and a real
    Q&A load that naturally needs four comes back to three, but a
    document that needs four against a two page budget does not (see
    test_the_page_fitter_actually_compacts_overflowing_content, sized to
    the real limit rather than to a range this doesn't have).

    Which side of the net a report lands on depends on whether it has a
    Q&A page. Without one it fits the budget at natural size and this is
    a safety net. With one, compaction is the NORMAL path rather than
    the exception, because the length of that page is a property of the
    call: past roughly eight dodges and six concessions even the tightest
    step overruns, and the report grows to four pages rather than losing
    a row. That is the intended trade.
    """
    rendered = render_pdf(html)
    if page_count(rendered) <= max_pages:
        return rendered
    for css in _COMPACTION_STEPS:
        rendered = render_pdf(_with_override_css(html, css))
        if page_count(rendered) <= max_pages:
            return rendered
    return rendered


def save_pdf(html: str, output_path: Union[str, Path], max_pages: int = None) -> int:
    """
    Renders `html` to PDF, writes it to `output_path`, and returns how
    many pages it actually has.

    With `max_pages`, compacts the layout as needed to fit that budget
    (see `render_pdf_fitted`); without it, renders at natural length --
    which is what the unbounded "détail" report wants.

    THE RETURN VALUE IS COUNTED, NOT ASSUMED. `render_pdf_fitted` keeps
    the longest render rather than dropping content, so asking for three
    pages is a request and not a promise, and a caller that announced
    "3 pages" from the argument it passed in would be reporting its own
    intention as a fact. Counting the real `/Type /Page` objects is the
    only way to say what was written.
    """
    rendered = (render_pdf(html) if max_pages is None
                else render_pdf_fitted(html, max_pages))
    Path(output_path).write_bytes(rendered)
    return page_count(rendered)
