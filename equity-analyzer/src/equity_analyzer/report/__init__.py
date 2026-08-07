"""
Module 5 -- Report Builder: assembles Modules 1-4's outputs into one
report, rendered to HTML and/or PDF. Includes multi-year trend analysis
(trend.py) built on top of the same single-period report builder, and
an opt-in AI summary (ai_summary.py, Module 6) layered on top via
`attach_ai_summary` -- never called by `build_report_data` itself.
"""

from .ai_summary import AISummaryError, attach_ai_summary
from .theme_selection import ThemeSelection, attach_theme_selection, select_key_subthemes
from .errors import PdfRenderError, ReportError
from .html_renderer import render_detail_html, render_html, render_trend_html
from .pdf_renderer import page_count, render_pdf, render_pdf_fitted, save_pdf
from .report_data import (
    FinancialHighlight,
    ReportData,
    SectionResult,
    build_report_data,
)
from .risk_alert import RiskFactorsAlert, evaluate_risk_alert
from .trend import TrendAnalysis, TrendPoint, build_trend_analysis

__all__ = [
    "ReportError",
    "PdfRenderError",
    "ReportData",
    "SectionResult",
    "FinancialHighlight",
    "build_report_data",
    "render_html",
    "render_detail_html",
    "render_pdf",
    "render_pdf_fitted",
    "page_count",
    "save_pdf",
    "TrendAnalysis",
    "TrendPoint",
    "build_trend_analysis",
    "render_trend_html",
    "AISummaryError",
    "attach_ai_summary",
    "ThemeSelection",
    "attach_theme_selection",
    "select_key_subthemes",
    "RiskFactorsAlert",
    "evaluate_risk_alert",
]
