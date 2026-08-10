"""
The page budget the entry point actually passes to the fitter.

WHY THIS IS A TEST OF ITS OWN. Every other layout test in this suite
hands `render_pdf_fitted` a page count written into the test. That
proves the fitter works; it proves nothing about the number production
gives it, and the two came apart once. When the Q&A page was added the
script's budget went from two to a flat three, and the case where a
report with no Q&A page overflows page 1 stopped being compacted: the
fitter, asked for three, saw a three page document and returned it
unchanged, so the reading spilled onto its own sheet and the numbers
were pushed to a third. Every layout test stayed green throughout,
because each was still passing its own hardcoded budget.

So this reads the constants out of the entry point and renders against
them. It is the seam between "the fitter honours a budget" and "the
budget is the right one".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from equity_analyzer.report.html_renderer import MAX_READING_WORDS, render_html
from equity_analyzer.report.pdf_renderer import page_count, render_pdf, render_pdf_fitted

from .test_call_report import _filler, build_report, needs_report_font

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "rapport.py"


def _entry_point():
    """The script, loaded by path: `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("rapport_entry_point", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_budget_is_two_without_a_qa_page_and_three_with_one():
    """
    Not one flat number. A document with no Q&A page holds two pages of
    content and is held to two; the third is bounded, not granted.
    """
    script = _entry_point()
    assert script.MAX_PAGES_WITHOUT_QA == 2
    assert script.MAX_PAGES_WITH_QA == 3


@needs_report_font
def test_a_report_without_a_qa_page_is_compacted_at_the_budget_production_uses():
    """
    The regression itself, rendered.

    The tight case: a stale call AND a period mismatch warning together
    cost page 1 around sixty words of room, so a reading at the cap
    overflows at natural size. Compacted at the budget the script really
    passes, it has to come back to two. Asserted through the script's
    constant rather than a literal 2, so flattening the budget back to a
    single number fails here.
    """
    report = build_report(
        reading=_filler(MAX_READING_WORDS - 1),
        quarters_back=2,
        period_warning=(
            "demandé 2026Q1 mais la société annonce 2025Q3 (« third quarter of fiscal "
            "2025 »), appariement à vérifier avant de conclure quoi que ce soit"
        ),
    )
    html = render_html(report)
    assert report.qa_analysis is None
    assert page_count(render_pdf(html)) > 2, "ce cas doit vraiment déborder au naturel"

    budget = _entry_point().MAX_PAGES_WITHOUT_QA
    assert page_count(render_pdf_fitted(html, max_pages=budget)) == 2
