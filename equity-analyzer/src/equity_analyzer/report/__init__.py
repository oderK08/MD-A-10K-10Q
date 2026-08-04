"""
Module 5 -- Report Builder: assembles Modules 1-4's outputs into one
report, rendered to HTML and/or PDF. Includes multi-year trend analysis
(trend.py) built on top of the same single-period report builder.
"""

from .errors import PdfRenderError, ReportError
from .html_renderer import render_html, render_trend_html
from .pdf_renderer import render_pdf, save_pdf
from .report_data import (
    FinancialHighlight,
    ReportData,
    SectionResult,
    build_report_data,
)
from .trend import TrendAnalysis, TrendPoint, build_trend_analysis

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
    "TrendAnalysis",
    "TrendPoint",
    "build_trend_analysis",
    "render_trend_html",
]
