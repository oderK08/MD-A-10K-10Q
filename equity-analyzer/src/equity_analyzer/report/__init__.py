"""
The report: one ticker in, one two-page PDF out.

Page 1 is Claude's reading of the latest earnings call, written against
the consensus the quarter was measured on. Page 2 is the numbers that do
not come from the call: the annual red flags and the Loughran-McDonald
tone of the call and of the quarter's MD&A.
"""

from .call_analysis import CallAnalysis, analyse_call, build_prompt
from .claude_client import ClaudeError, DEFAULT_MODEL, call_claude
from .errors import PdfRenderError, ReportError
from .html_renderer import MAX_READING_WORDS, render_html
from .markdown import markdown_to_html, truncate_words
from .pdf_renderer import page_count, render_pdf, render_pdf_fitted, save_pdf
from .report_data import CallInfo, CallReport, SectionResult, build_call_report

__all__ = [
    "CallAnalysis",
    "CallInfo",
    "CallReport",
    "ClaudeError",
    "DEFAULT_MODEL",
    "MAX_READING_WORDS",
    "PdfRenderError",
    "ReportError",
    "SectionResult",
    "analyse_call",
    "build_call_report",
    "build_prompt",
    "call_claude",
    "markdown_to_html",
    "page_count",
    "render_html",
    "render_pdf",
    "render_pdf_fitted",
    "save_pdf",
    "truncate_words",
]
