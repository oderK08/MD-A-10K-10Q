"""
Module 5 -- Report Builder: assembles Modules 1-4's outputs into one
report, rendered to HTML and/or PDF.
"""

from .errors import PdfRenderError, ReportError
from .html_renderer import render_html
from .pdf_renderer import render_pdf, save_pdf
from .report_data import (
    FinancialHighlight,
    ReportData,
    SectionResult,
    build_report_data,
)

__all__ = [
    "ReportError",
    "PdfRenderError",
    "ReportData",
    "SectionResult",
    "FinancialHighlight",
    "build_report_data",
    "render_html",
    "render_pdf",
    "save_pdf",
]
