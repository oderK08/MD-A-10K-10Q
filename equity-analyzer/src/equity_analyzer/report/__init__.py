"""
The report: one ticker in, one PDF out.

Page 1 is Claude's reading of the latest earnings call, written against
the consensus the quarter was measured on. Page 2 is the same call taken
apart: what was dodged, what was conceded, what carried forward looking
value and was not in the press release. Page 3 is the numbers that do
not come from the call: the annual red flags and the Loughran-McDonald
tone of the call and of the quarter's MD&A.

Page 2 is dropped entirely when the transcript has no Q&A half to read,
because a page of empty headings is worse than no page, so a report
without one is exactly two pages.
"""

from .call_analysis import CallAnalysis, analyse_call, build_prompt
from .qa_analysis import QaAnalysis, analyse_qa
from .claude_client import ClaudeError, DEFAULT_MODEL, call_claude
from .errors import PdfRenderError, ReportError
from .html_renderer import MAX_READING_WORDS, render_html
from .markdown import markdown_to_html, truncate_words
from .pdf_renderer import page_count, render_pdf, render_pdf_fitted, save_pdf
from .report_data import CallInfo, CallReport, SectionResult, build_call_report

__all__ = [
    "CallAnalysis",
    "QaAnalysis",
    "analyse_qa",
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
