"""Test-only helpers for building Filing objects directly."""

from __future__ import annotations

from datetime import date

from equity_analyzer.data_layer.models import (
    FactValue,
    Filing,
    FilingTextSections,
    FinancialPeriod,
    FormType,
    PeriodDuration,
)

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


def make_financial_period(
    *,
    fiscal_year: int = 2024,
    fiscal_period: str = "FY",
    duration: PeriodDuration = PeriodDuration.TWELVE_MONTH,
    accession_number: str = "0000000000-00-000001",
    period_end: date = date(2024, 12, 31),
    **metrics: float,
) -> FinancialPeriod:
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


def make_filing(
    *,
    ticker: str = "TEST",
    cik: str = "0000000001",
    company_name: str = "Test Company Inc.",
    form_type: FormType = FormType.TEN_K,
    fiscal_year: int = 2024,
    fiscal_period: str = "FY",
    filed_date: date = date(2025, 2, 1),
    accession_number: str = "0000000000-00-000001",
    period_end: date = date(2024, 12, 31),
    financials: FinancialPeriod = None,
    text_sections: FilingTextSections = None,
) -> Filing:
    return Filing(
        ticker=ticker,
        cik=cik,
        company_name=company_name,
        form_type=form_type,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        filed_date=filed_date,
        accession_number=accession_number,
        period_end=period_end,
        financials=financials,
        text_sections=text_sections,
    )


def make_transcript(
    *,
    ticker: str = "TEST",
    prepared: str = "Revenue grew strongly this quarter and margins improved.",
    qa: str = "Analyst question about pricing. Management gave a vague answer.",
    source: str = "Alpha Vantage",
    call_date: date = date(2026, 2, 3),
    fiscal_period: str = "2026Q1",
):
    from equity_analyzer.data_layer.transcript_source import CallTranscript

    full = prepared if qa is None else f"{prepared}\n{qa}"
    return CallTranscript(
        ticker=ticker,
        call_date=call_date,
        fiscal_period=fiscal_period,
        full_text=full,
        prepared_remarks=prepared,
        qa=qa,
        source=source,
    )


def make_analysis(
    *,
    ticker: str = "TEST",
    quarter: str = "2026Q1",
    text: str = "## Verdict\nPlutot bullish, la guidance monte.\n\n## Face aux attentes\nBeat de 8%.",
    model: str = "claude-sonnet-5",
    transcript_words: int = 8000,
    had_expectations: bool = True,
):
    from equity_analyzer.report.call_analysis import CallAnalysis

    return CallAnalysis(
        ticker=ticker,
        quarter=quarter,
        text=text,
        model=model,
        transcript_words=transcript_words,
        had_expectations=had_expectations,
    )


def make_expectation(
    *,
    period_end: date = date(2025, 12, 31),
    estimated_eps: float = 1.10,
    reported_eps: float = 1.19,
    surprise: float = 0.09,
    surprise_pct: float = 8.2,
    reported_date: date = date(2026, 2, 3),
):
    from equity_analyzer.data_layer.earnings_expectations import QuarterExpectation

    return QuarterExpectation(
        fiscal_date_ending=period_end,
        reported_date=reported_date,
        estimated_eps=estimated_eps,
        reported_eps=reported_eps,
        surprise=surprise,
        surprise_pct=surprise_pct,
    )
