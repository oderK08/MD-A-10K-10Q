"""
Test-only helpers for building FinancialPeriod objects directly (no raw
XBRL JSON needed -- Module 2 operates purely on already-normalized
FinancialPeriod data, so tests exercise it at that level).
"""

from __future__ import annotations

from datetime import date

from equity_analyzer.data_layer.models import FactValue, FinancialPeriod, PeriodDuration

_METRIC_NAMES = {
    "revenue", "cogs", "gross_profit", "sga_expense",
    "depreciation_amortization", "operating_income", "net_income",
    "total_assets", "current_assets", "receivables", "ppe_net",
    "total_liabilities", "current_liabilities", "long_term_debt",
    "retained_earnings", "total_equity", "shares_outstanding",
    "operating_cash_flow", "capex",
}


def make_fact(value: float, *, period_end: date, accession: str) -> FactValue:
    return FactValue(
        concept="TestConcept",
        value=value,
        unit="USD",
        period_start=None,
        period_end=period_end,
        duration=PeriodDuration.INSTANT,
        accession_number=accession,
        filed_date=period_end,
    )


def make_period(
    *,
    fiscal_year: int = 2024,
    fiscal_period: str = "FY",
    duration: PeriodDuration = PeriodDuration.TWELVE_MONTH,
    accession_number: str = "0000000000-00-000001",
    period_end: date = date(2024, 12, 31),
    **metrics: float,
) -> FinancialPeriod:
    """
    Builds a FinancialPeriod with the given top-level fields, wrapping every
    keyword metric (e.g. total_assets=1_000_000) into a FactValue. Metrics
    not passed default to None, matching real-world "not reported" data.
    """
    unknown = set(metrics) - _METRIC_NAMES
    if unknown:
        raise ValueError(f"Unknown FinancialPeriod metric(s): {unknown}")

    facts = {
        name: make_fact(value, period_end=period_end, accession=accession_number)
        for name, value in metrics.items()
    }

    return FinancialPeriod(
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        duration=duration,
        period_start=None,
        period_end=period_end,
        accession_number=accession_number,
        filed_date=period_end,
        **facts,
    )
