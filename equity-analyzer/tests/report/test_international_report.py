"""
The reduced document for a company outside the SEC's reach.

WHAT MATTERS HERE. An international report is NOT the US report with a
blank page 3. Page 3 is dropped entirely, because its inputs (a 10-K to
score red flags on, an MD&A to read, an English lexicon that fits an
English filing) do not exist here, and a page of "non disponible" is
noise rather than information. What survives is the reading and the Q&A,
which need only the call. These tests pin that the EDGAR-shaped claims
disappear with the page.
"""

from __future__ import annotations

from datetime import datetime, timezone

from equity_analyzer.report.html_renderer import render_html
from equity_analyzer.report.pdf_renderer import page_count, render_pdf
from equity_analyzer.report.report_data import build_call_report

from .factories import make_analysis, make_transcript

READING = "## Verdict\nPlutot bullish. " + ("mot " * 220)


def _international_report(**overrides):
    kwargs = dict(
        call_quarter="2026Q2",
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        international=True,
    )
    kwargs.update(overrides)
    return build_call_report(
        "SAP", "SAP SE", "",
        make_transcript(
            prepared="Revenue grew across cloud on strong backlog conversion. " * 4,
            qa="An analyst asked about the margin outlook. The answer stayed vague. " * 4,
        ),
        make_analysis(text=READING),
        **kwargs,
    )


def test_page_three_is_absent_entirely():
    """
    Not shown as unavailable, gone. The red flags need a 10-K, the MD&A
    tone needs a 10-Q, and the Loughran-McDonald lexicon is English by
    construction. None of that exists here.
    """
    html = render_html(_international_report())

    assert "Red flags" not in html
    assert "Altman" not in html
    assert "Beneish" not in html
    assert "Tonalité" not in html


def test_it_claims_no_sec_source_it_does_not_have():
    """
    The provenance footer must not say "Chiffres : SEC EDGAR" when there
    are none, and the header must not print an empty CIK line.
    """
    html = render_html(_international_report())

    assert "SEC EDGAR" not in html
    assert "CIK" not in html


def test_the_reading_and_the_qa_still_render():
    """What survives is exactly what needs only the call."""
    from .test_call_report import _qa  # the shared Q&A factory
    import dataclasses

    report = dataclasses.replace(_international_report(), qa_analysis=_qa())
    html = render_html(report)

    assert "Verdict" in html
    assert "Questions et réponses" in html or "esquive" in html.lower()


def test_a_reduced_report_without_a_qa_page_is_at_most_two_pages():
    """
    Reading plus provenance, no page 3 and no forced blank sheet. The
    document should not run long just because the accounting page is
    gone.
    """
    pdf = render_pdf(render_html(_international_report()))
    assert page_count(pdf) <= 2


def test_the_consensus_absence_reads_as_out_of_scope_not_as_a_failure():
    """
    The reading was written without a consensus, and the reader should
    know that. It is stated as out of scope rather than hidden, the same
    discipline the US path applies to any missing input.
    """
    html = render_html(_international_report(
        expectations_reason="hors périmètre SEC : pas de consensus comparable"
    ))
    assert "hors périmètre" in html.lower()


def test_the_same_report_built_us_style_still_has_page_three():
    """
    The flag, and only the flag, controls the drop. Built without it the
    identical data keeps its third page, so nothing here leaks into the
    US path.
    """
    from .test_call_report import build_report

    assert "Red flags" in render_html(build_report())
