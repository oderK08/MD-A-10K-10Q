"""
Assembling the report object.

The thing under test is mostly a set of refusals: which numbers are NOT
computed, and whether an absence arrives as a printable reason instead
of a blank.
"""

from __future__ import annotations

from datetime import date

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.report.report_data import build_call_report
from equity_analyzer.sentiment.lm_dictionary import load_lm_dictionary

from .factories import make_analysis, make_expectation, make_filing, make_transcript
from ..redflags.factories import make_period

from pathlib import Path

DICTIONARY = load_lm_dictionary(
    Path(__file__).parent.parent / "fixtures" / "sample_lm_dictionary.csv"
)

_ANNUAL_PRIOR = dict(
    net_income=50_000, total_assets=1_000_000, long_term_debt=300_000,
    current_assets=400_000, current_liabilities=300_000, shares_outstanding=1_000_000,
    revenue=800_000, gross_profit=300_000, total_equity=500_000,
    total_liabilities=500_000, retained_earnings=200_000, operating_income=90_000,
)
_ANNUAL_CURRENT = dict(
    net_income=80_000, total_assets=1_000_000, operating_cash_flow=100_000,
    long_term_debt=200_000, current_assets=500_000, current_liabilities=250_000,
    shares_outstanding=1_000_000, revenue=1_000_000, gross_profit=450_000,
    total_equity=600_000, total_liabilities=400_000, retained_earnings=280_000,
    operating_income=150_000,
)


def _annual(year=2024, **metrics):
    return make_filing(
        form_type=FormType.TEN_K,
        fiscal_year=year,
        fiscal_period="FY",
        period_end=date(year, 12, 31),
        financials=make_period(
            duration=PeriodDuration.TWELVE_MONTH,
            fiscal_year=year,
            fiscal_period="FY",
            period_end=date(year, 12, 31),
            accession_number=f"acc-{year}",
            **metrics,
        ),
    )


def _quarter(sections=None):
    return make_filing(
        form_type=FormType.TEN_Q,
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2025, 12, 31),
        text_sections=sections,
    )


def _build(**overrides):
    kwargs = dict(
        ticker="TEST",
        company_name="Test Company Inc.",
        cik="0000000001",
        transcript=make_transcript(),
        analysis=make_analysis(),
        call_quarter="2026Q1",
        lm_dictionary=DICTIONARY,
    )
    kwargs.update(overrides)
    transcript = kwargs.pop("transcript")
    analysis = kwargs.pop("analysis")
    return build_call_report(
        kwargs.pop("ticker"), kwargs.pop("company_name"), kwargs.pop("cik"),
        transcript, analysis, **kwargs,
    )


# -- Red flags ----------------------------------------------------------


def test_red_flags_are_computed_from_the_two_annual_filings():
    report = _build(
        annual_filing=_annual(2024, **_ANNUAL_CURRENT),
        prior_annual_filing=_annual(2023, **_ANNUAL_PRIOR),
    )
    assert report.altman_z.available
    assert report.beneish_m.available or report.beneish_m.unavailable_reason
    assert report.piotroski_f.available
    assert report.piotroski_f.value.score == 9


def test_no_annual_filing_means_no_scores_and_says_why():
    """
    NOT a fallback opportunity. Altman, Beneish and Piotroski were all
    estimated on full-year statements; run on a quarter they do not
    produce a noisier number, they produce a category error that looks
    exactly like a good one.
    """
    report = _build(quarter_filing=_quarter())

    for section in (report.altman_z, report.beneish_m, report.piotroski_f):
        assert not section.available
        assert "annuel" in section.unavailable_reason


def test_one_annual_filing_still_gives_altman_but_not_the_year_over_year_pair():
    """
    Altman needs one year, Beneish and Piotroski need two. Losing all
    three because the second 10-K is missing would throw away a score
    that was computable.
    """
    report = _build(annual_filing=_annual(2024, **_ANNUAL_CURRENT))

    assert report.altman_z.available
    assert not report.beneish_m.available
    assert "exercice précédent" in report.beneish_m.unavailable_reason


def test_a_missing_metric_becomes_a_printable_reason_not_an_exception():
    report = _build(
        annual_filing=_annual(2024, net_income=1, total_assets=2),
        prior_annual_filing=_annual(2023, net_income=1, total_assets=2),
    )
    assert not report.altman_z.available
    assert report.altman_z.unavailable_reason


# -- Tone ---------------------------------------------------------------


def test_the_call_is_scored_in_two_halves_not_one():
    """
    Prepared remarks are written, lawyered and rehearsed, so their tone
    is a decision management made. The Q&A is unscripted. Scoring the
    call as one block averages those two into a number that describes
    neither.
    """
    report = _build()
    assert report.tone_prepared.available
    assert report.tone_qa.available
    assert report.tone_prepared.value.total_word_count < report.call.word_count


def test_a_call_with_no_isolable_qa_says_so_rather_than_scoring_zero():
    report = _build(transcript=make_transcript(qa=None))
    assert not report.tone_qa.available
    assert "questions" in report.tone_qa.unavailable_reason


def test_no_dictionary_means_no_tone_and_says_why():
    report = _build(lm_dictionary=None)
    assert not report.tone_prepared.available
    assert "Loughran-McDonald" in report.tone_prepared.unavailable_reason


def test_the_mdna_is_scored_when_the_quarter_filing_was_read():
    sections = FilingTextSections(
        item_1a_risk_factors=None,
        item_7_mdna="Revenue increased and margins improved on strong demand.",
        item_9a_controls=None,
        is_risk_factors_boilerplate=False,
    )
    report = _build(quarter_filing=_quarter(sections))
    assert report.tone_mdna.available


def test_no_quarter_filing_means_no_mdna_tone_and_says_why():
    report = _build()
    assert not report.tone_mdna.available
    assert "10-Q" in report.tone_mdna.unavailable_reason


# -- Expectations -------------------------------------------------------


def test_an_expectation_is_carried_through_with_its_history():
    history = [make_expectation(period_end=date(2025, 9, 30))]
    report = _build(expectation=make_expectation(), expectations_history=history)

    assert report.expectations.available
    assert report.expectations.value.verdict == "au-dessus"
    assert len(report.expectations_history) == 1


def test_a_missing_expectation_carries_the_reason_it_is_missing():
    """
    "We asked and could not get it" and "nobody asked" are different
    statements, and the reader can only tell them apart if the report
    prints which one happened.
    """
    report = _build(expectations_reason="quota Alpha Vantage épuisé")
    assert not report.expectations.available
    assert "quota" in report.expectations.unavailable_reason


def test_no_expectation_and_no_reason_still_produces_a_printable_sentence():
    report = _build()
    assert not report.expectations.available
    assert report.expectations.unavailable_reason


# -- Provenance ---------------------------------------------------------


def test_a_stale_call_is_recorded_as_stale():
    """
    A quarter old transcript is still useful. A quarter old transcript
    believed to be current is not.
    """
    report = _build(quarters_back=2, period_warning="demandé 2026Q1 mais la société annonce 2025Q3")

    assert report.call.quarters_back == 2
    assert "2025Q3" in report.call.period_warning
