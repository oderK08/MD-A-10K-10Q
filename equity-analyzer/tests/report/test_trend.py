from datetime import date

import pytest

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.report.trend import build_trend_analysis
from equity_analyzer.sentiment.lm_dictionary import LMDictionary

from .factories import make_filing, make_financial_period

DICTIONARY = LMDictionary(
    words_by_category={
        "negative": frozenset({"decline"}),
        "positive": frozenset({"growth"}),
        "uncertainty": frozenset(), "litigious": frozenset(),
        "strong_modal": frozenset(), "weak_modal": frozenset(),
        "constraining": frozenset(),
    }
)

_METRICS = dict(
    revenue=1_200_000, cogs=700_000, gross_profit=500_000, sga_expense=100_000,
    depreciation_amortization=50_000, operating_income=150_000, net_income=100_000,
    total_assets=1_000_000, current_assets=600_000, receivables=150_000,
    ppe_net=300_000, total_liabilities=400_000, current_liabilities=200_000,
    long_term_debt=100_000, retained_earnings=300_000, total_equity=600_000,
    shares_outstanding=1_000_000, operating_cash_flow=120_000,
)


def _filing_for_year(year: int) -> object:
    financials = make_financial_period(
        fiscal_year=year, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
        accession_number=f"acc-{year}", period_end=date(year, 12, 31), **_METRICS,
    )
    text_sections = FilingTextSections(
        item_1a_risk_factors="our revenue could decline due to competition",
        item_7_mdna="revenue grew due to strong growth this year",
        is_risk_factors_boilerplate=False,
    )
    return make_filing(
        fiscal_year=year, fiscal_period="FY", form_type=FormType.TEN_K,
        accession_number=f"acc-{year}", period_end=date(year, 12, 31),
        financials=financials, text_sections=text_sections,
    )


def test_builds_one_point_per_filing_oldest_to_newest():
    filings = [_filing_for_year(y) for y in (2021, 2022, 2023)]
    trend = build_trend_analysis(filings, DICTIONARY)

    assert [p.fiscal_year for p in trend.points] == [2021, 2022, 2023]


def test_oldest_point_has_no_prior_so_only_altman_and_sentiment_available():
    filings = [_filing_for_year(y) for y in (2021, 2022)]
    trend = build_trend_analysis(filings, DICTIONARY)

    oldest = trend.points[0]
    assert oldest.report.altman_z.available
    assert oldest.report.mdna_sentiment.available
    assert not oldest.report.beneish_m.available
    assert not oldest.report.piotroski_f.available
    assert not oldest.report.mdna_diff.available


def test_later_points_compare_against_the_immediately_preceding_filing():
    filings = [_filing_for_year(y) for y in (2021, 2022, 2023)]
    trend = build_trend_analysis(filings, DICTIONARY)

    point_2023 = trend.points[2]
    assert point_2023.report.beneish_m.available
    # the prior filing used for the 2023 point's comparison must be the
    # 2022 one, not 2021 -- i.e. no year is skipped.
    assert point_2023.report.prior_filing.fiscal_year == 2022


def test_raises_with_fewer_than_two_filings():
    with pytest.raises(ValueError, match="at least 2 filings"):
        build_trend_analysis([_filing_for_year(2023)], DICTIONARY)


def test_raises_when_filings_not_strictly_ascending():
    filings = [_filing_for_year(2023), _filing_for_year(2022)]
    with pytest.raises(ValueError, match="oldest -> newest"):
        build_trend_analysis(filings, DICTIONARY)


def test_raises_on_duplicate_fiscal_year():
    filings = [_filing_for_year(2022), _filing_for_year(2022)]
    with pytest.raises(ValueError, match="oldest -> newest"):
        build_trend_analysis(filings, DICTIONARY)
