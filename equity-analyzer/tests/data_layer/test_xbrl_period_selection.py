"""
Tests for picking the RIGHT period out of a filing's facts.

One accession tags many periods, not one (see xbrl_normalizer's module
docstring, point 3): a 10-K carries three years of comparative income
statement, a 10-Q carries both the quarter alone and the year-to-date
cumulative plus the year-ago columns, and every one of those is stamped
with the same accession number. Taking whichever entry comes first in
the array returns an arbitrary period -- in practice the oldest
comparative, because companyfacts orders entries by end date ascending.

The failure mode this guards against is silent by construction: the
number that comes back is a real number the company really reported, it
is simply for a period nobody asked about, and nothing downstream can
tell.
"""

from datetime import date

from equity_analyzer.data_layer.models import PeriodDuration
from equity_analyzer.data_layer.xbrl_normalizer import (
    build_financial_period,
    report_period_for_accession,
)

ANNUAL_ACCESSION = "0000000000-25-000010"
QUARTER_ACCESSION = "0000000000-26-000020"


def _annual_facts():
    """
    A 10-K as companyfacts really presents one: the current fiscal year
    AND the two comparative years, all under the filing's own accession,
    ordered oldest first.
    """
    return {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2022-10-01", "end": "2023-09-30", "val": 300, "fy": 2025,
             "fp": "FY", "accn": ANNUAL_ACCESSION, "filed": "2025-10-30"},
            {"start": "2023-10-01", "end": "2024-09-30", "val": 400, "fy": 2025,
             "fp": "FY", "accn": ANNUAL_ACCESSION, "filed": "2025-10-30"},
            {"start": "2024-10-01", "end": "2025-09-30", "val": 500, "fy": 2025,
             "fp": "FY", "accn": ANNUAL_ACCESSION, "filed": "2025-10-30"},
        ]}},
        "Assets": {"units": {"USD": [
            {"end": "2024-09-30", "val": 8000, "fy": 2025, "fp": "FY",
             "accn": ANNUAL_ACCESSION, "filed": "2025-10-30"},
            {"end": "2025-09-30", "val": 9000, "fy": 2025, "fp": "FY",
             "accn": ANNUAL_ACCESSION, "filed": "2025-10-30"},
        ]}},
    }}}


def _quarter_facts():
    """
    A Q3 10-Q: the quarter alone (3M) and the year-to-date cumulative
    (9M) for the current period, plus both of last year's equivalents,
    and a balance sheet showing the quarter end and the prior year end.
    """
    return {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2024-10-01", "end": "2025-06-30", "val": 900, "fy": 2026,
             "fp": "Q3", "accn": QUARTER_ACCESSION, "filed": "2026-07-10"},
            {"start": "2025-04-01", "end": "2025-06-30", "val": 300, "fy": 2026,
             "fp": "Q3", "accn": QUARTER_ACCESSION, "filed": "2026-07-10"},
            {"start": "2025-10-01", "end": "2026-06-30", "val": 1200, "fy": 2026,
             "fp": "Q3", "accn": QUARTER_ACCESSION, "filed": "2026-07-10"},
            {"start": "2026-04-01", "end": "2026-06-30", "val": 450, "fy": 2026,
             "fp": "Q3", "accn": QUARTER_ACCESSION, "filed": "2026-07-10"},
        ]}},
        "Assets": {"units": {"USD": [
            {"end": "2025-09-30", "val": 9000, "fy": 2026, "fp": "Q3",
             "accn": QUARTER_ACCESSION, "filed": "2026-07-10"},
            {"end": "2026-06-30", "val": 9500, "fy": 2026, "fp": "Q3",
             "accn": QUARTER_ACCESSION, "filed": "2026-07-10"},
        ]}},
    }}}


def test_annual_filing_takes_the_current_year_not_a_comparative():
    period = build_financial_period(
        _annual_facts(), ANNUAL_ACCESSION, fiscal_year=2025, fiscal_period="FY"
    )
    assert period.net_income.value == 500
    assert period.net_income.period_end == date(2025, 9, 30)
    assert period.net_income.duration == PeriodDuration.TWELVE_MONTH


def test_annual_balance_sheet_takes_the_closing_snapshot():
    period = build_financial_period(
        _annual_facts(), ANNUAL_ACCESSION, fiscal_year=2025, fiscal_period="FY"
    )
    assert period.total_assets.value == 9000
    assert period.total_assets.period_end == date(2025, 9, 30)


def test_quarterly_filing_takes_the_quarter_alone_not_year_to_date():
    """
    The single most damaging confusion in quarterly data: 1200 (nine
    months) and 450 (the quarter) are both correct figures the company
    really reported, and nothing about the value itself says which is
    which. Comparing a year-to-date figure against last quarter's
    quarter-alone figure would show growth that is pure arithmetic.
    """
    period = build_financial_period(
        _quarter_facts(), QUARTER_ACCESSION, fiscal_year=2026, fiscal_period="Q3"
    )
    assert period.net_income.value == 450
    assert period.net_income.duration == PeriodDuration.THREE_MONTH
    assert period.net_income.period_start == date(2026, 4, 1)


def test_quarterly_balance_sheet_takes_the_quarter_end_not_the_prior_year_end():
    period = build_financial_period(
        _quarter_facts(), QUARTER_ACCESSION, fiscal_year=2026, fiscal_period="Q3"
    )
    assert period.total_assets.value == 9500
    assert period.total_assets.period_end == date(2026, 6, 30)


def test_fiscal_period_is_read_off_the_filing_when_not_supplied():
    """
    The submissions endpoint doesn't say which quarter a 10-Q is, and the
    calendar can't be used to guess it for a filer whose fiscal year ends
    in September: a period ending 30 June is Q3 for them and Q2 for a
    December filer. The filing's own XBRL labels answer it.
    """
    assert report_period_for_accession(_quarter_facts(), QUARTER_ACCESSION) == (2026, "Q3")

    period = build_financial_period(_quarter_facts(), QUARTER_ACCESSION)
    assert period.fiscal_period == "Q3"
    assert period.fiscal_year == 2026
    # ...and the derived period drives the duration choice, so an
    # un-annotated call still gets the quarter and not the cumulative.
    assert period.net_income.value == 450


def test_unlabelled_filing_is_treated_as_annual():
    """
    Filings with no fp label keep the pre-quarterly behaviour: 12-month
    facts. That's the shape everything in this project read before
    quarterly support existed, so it's what stays correct by default.
    """
    facts = _annual_facts()
    for entries in facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]:
        entries.pop("fp")
        entries.pop("fy")
    period = build_financial_period(facts, ANNUAL_ACCESSION, fiscal_year=2025)
    assert period.net_income.duration == PeriodDuration.TWELVE_MONTH
    assert period.net_income.value == 500


def test_a_metric_reported_only_year_to_date_is_returned_labelled_not_dropped():
    """
    Some filers tag a concept only cumulatively. Returning None there
    would lose a real disclosure; returning it silently as if it were the
    quarter would be worse. It comes back with its true duration on it,
    for a caller to check.
    """
    facts = _quarter_facts()
    facts["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": [
        {"start": "2025-10-01", "end": "2026-06-30", "val": 7777, "fy": 2026,
         "fp": "Q3", "accn": QUARTER_ACCESSION, "filed": "2026-07-10"},
    ]}}
    period = build_financial_period(facts, QUARTER_ACCESSION)
    assert period.revenue.value == 7777
    assert period.revenue.duration == PeriodDuration.NINE_MONTH
