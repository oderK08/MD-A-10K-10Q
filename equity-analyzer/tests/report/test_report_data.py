from datetime import date

import pytest

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.report.report_data import build_report_data
from equity_analyzer.sentiment.lm_dictionary import LMDictionary

from .factories import make_filing, make_financial_period

# A complete, mutually-comparable pair of annual FinancialPeriods --
# enough fields for Altman, Beneish, AND Piotroski to all compute
# successfully, so tests can isolate what happens when prior_filing /
# lm_dictionary / financials are withheld, rather than fighting missing
# metrics unrelated to what's being tested.
_FULL_METRICS = dict(
    revenue=1_200_000,
    cogs=700_000,
    gross_profit=500_000,
    sga_expense=100_000,
    depreciation_amortization=50_000,
    operating_income=150_000,
    net_income=100_000,
    total_assets=1_000_000,
    current_assets=600_000,
    receivables=150_000,
    ppe_net=300_000,
    total_liabilities=400_000,
    current_liabilities=200_000,
    long_term_debt=100_000,
    retained_earnings=300_000,
    total_equity=600_000,
    shares_outstanding=1_000_000,
    operating_cash_flow=120_000,
)

DICTIONARY = LMDictionary(
    words_by_category={
        "negative": frozenset({"decline"}),
        "positive": frozenset({"growth"}),
        "uncertainty": frozenset(),
        "litigious": frozenset(),
        "strong_modal": frozenset(),
        "weak_modal": frozenset(),
        "constraining": frozenset(),
    }
)


def _current_filing(**overrides):
    financials = make_financial_period(
        fiscal_year=2024, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
        accession_number="acc-current", period_end=date(2024, 12, 31), **_FULL_METRICS,
    )
    text_sections = FilingTextSections(
        item_1a_risk_factors="our revenue could decline due to competition",
        item_7_mdna="revenue grew due to strong growth this year",
        is_risk_factors_boilerplate=False,
    )
    kwargs = dict(
        fiscal_year=2024, fiscal_period="FY", form_type=FormType.TEN_K,
        accession_number="acc-current", period_end=date(2024, 12, 31),
        financials=financials, text_sections=text_sections,
    )
    kwargs.update(overrides)
    return make_filing(**kwargs)


def _prior_filing(**overrides):
    financials = make_financial_period(
        fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
        accession_number="acc-prior", period_end=date(2023, 12, 31), **_FULL_METRICS,
    )
    text_sections = FilingTextSections(
        item_1a_risk_factors="our revenue could decline due to weak demand",
        item_7_mdna="revenue was flat this year",
        is_risk_factors_boilerplate=False,
    )
    kwargs = dict(
        fiscal_year=2023, fiscal_period="FY", form_type=FormType.TEN_K,
        accession_number="acc-prior", period_end=date(2023, 12, 31),
        financials=financials, text_sections=text_sections,
    )
    kwargs.update(overrides)
    return make_filing(**kwargs)


def test_all_sections_available_with_full_data():
    report = build_report_data(_current_filing(), _prior_filing(), DICTIONARY)

    assert report.altman_z.available
    assert report.beneish_m.available
    assert report.piotroski_f.available
    assert report.mdna_diff.available
    assert report.risk_factors_diff.available
    assert report.mdna_sentiment.available
    assert report.risk_factors_sentiment.available
    assert len(report.financial_highlights) == 7


def test_no_prior_filing_marks_yoy_sections_unavailable():
    report = build_report_data(_current_filing(), prior_filing=None, lm_dictionary=DICTIONARY)

    assert report.altman_z.available  # doesn't need a prior period
    assert not report.beneish_m.available
    assert "prior-period" in report.beneish_m.unavailable_reason
    assert not report.piotroski_f.available
    assert not report.mdna_diff.available
    assert not report.risk_factors_diff.available
    # sentiment doesn't need a prior period at all
    assert report.mdna_sentiment.available
    assert report.risk_factors_sentiment.available


def test_missing_financials_marks_red_flags_unavailable():
    current = _current_filing(financials=None)
    report = build_report_data(current, _prior_filing(), DICTIONARY)

    assert not report.altman_z.available
    assert "no financial data" in report.altman_z.unavailable_reason
    assert not report.beneish_m.available
    assert not report.piotroski_f.available
    assert report.financial_highlights == []
    # text-based sections are untouched by missing financials
    assert report.mdna_diff.available


def test_quarterly_financials_altman_unavailable_but_beneish_available():
    current = _current_filing(
        financials=make_financial_period(
            fiscal_year=2025, fiscal_period="Q2", duration=PeriodDuration.THREE_MONTH,
            accession_number="acc-current-q2", period_end=date(2025, 6, 30), **_FULL_METRICS,
        ),
        fiscal_year=2025, fiscal_period="Q2",
    )
    prior = _prior_filing(
        financials=make_financial_period(
            fiscal_year=2024, fiscal_period="Q2", duration=PeriodDuration.THREE_MONTH,
            accession_number="acc-prior-q2", period_end=date(2024, 6, 30), **_FULL_METRICS,
        ),
        fiscal_year=2024, fiscal_period="Q2",
    )

    report = build_report_data(current, prior, DICTIONARY)

    assert not report.altman_z.available
    assert "12-month" in report.altman_z.unavailable_reason
    assert report.beneish_m.available  # same-quarter YoY comparison is fine for Beneish
    assert report.piotroski_f.available


def test_no_dictionary_marks_sentiment_unavailable():
    report = build_report_data(_current_filing(), _prior_filing(), lm_dictionary=None)

    assert not report.mdna_sentiment.available
    assert "dictionary" in report.mdna_sentiment.unavailable_reason
    assert not report.risk_factors_sentiment.available
    # unrelated sections still compute
    assert report.altman_z.available
    assert report.mdna_diff.available


def test_boilerplate_risk_factors_thread_through_skip_not_unavailable():
    """
    A boilerplate current-period Item 1A is a deliberate SKIP (data was
    available, the module chose not to score/diff it), which is a
    different state than `unavailable` (no data / an error) -- both must
    be distinguishable downstream (e.g. by the renderer).
    """
    current = _current_filing()
    current.text_sections.is_risk_factors_boilerplate = True

    report = build_report_data(current, _prior_filing(), DICTIONARY)

    assert report.risk_factors_diff.available  # the computation ran...
    assert report.risk_factors_diff.value.skipped is True  # ...it just chose to skip
    assert report.risk_factors_sentiment.available
    assert report.risk_factors_sentiment.value.skipped is True
    # MD&A is unaffected by Item 1A being boilerplate
    assert report.mdna_diff.value.overall.segments  # still a real diff
