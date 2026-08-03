"""
Core data models for the equity analyzer.

Design decisions (see conversation for rationale):
- FinancialPeriod distinguishes `duration` (instant vs 3M/6M/9M/12M) explicitly,
  because 10-Q filings report multiple overlapping periods (quarter alone AND
  year-to-date cumulative) and silently mixing them corrupts every downstream
  ratio.
- Filing stores `form_type` (10-K or 10-Q) as data, not as a separate class,
  so the rest of the pipeline has one code path, not two.
- accession_number is kept on every value we pull from XBRL so we can always
  trace a number back to the exact filing it came from (auditability,
  important for advisory use) and so we can pick the ORIGINAL filed value
  rather than a later restatement unless we explicitly ask for the restated one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class FormType(str, Enum):
    TEN_K = "10-K"
    TEN_Q = "10-Q"


class PeriodDuration(str, Enum):
    """What kind of period a financial fact covers."""
    INSTANT = "instant"        # balance sheet snapshot (e.g. total assets at date X)
    THREE_MONTH = "3M"         # a single fiscal quarter
    SIX_MONTH = "6M"           # year-to-date, two quarters
    NINE_MONTH = "9M"          # year-to-date, three quarters
    TWELVE_MONTH = "12M"       # full fiscal year


@dataclass(frozen=True)
class FactValue:
    """A single XBRL fact: one number, with full provenance."""
    concept: str                 # the us-gaap tag actually used, e.g. "NetIncomeLoss"
    value: float
    unit: str                    # e.g. "USD"
    period_start: Optional[date]  # None for instant facts
    period_end: date
    duration: PeriodDuration
    accession_number: str         # which filing this value was reported in
    filed_date: date              # when that filing was filed
    frame: Optional[str] = None   # SEC "frame" identifier if available, else None


@dataclass
class FinancialPeriod:
    """
    Normalized set of financial statement figures for one company-period,
    resolved to a single candidate tag per metric (see xbrl_normalizer.py
    for the fallback logic) and a single duration.

    All fields are Optional because not every company reports every concept
    every period (e.g. some don't break out D&A separately).
    """
    fiscal_year: int
    fiscal_period: str            # "FY", "Q1", "Q2", "Q3", "Q4"
    duration: PeriodDuration
    period_start: Optional[date]
    period_end: date
    accession_number: str
    filed_date: date

    # Income statement
    revenue: Optional[FactValue] = None
    cogs: Optional[FactValue] = None
    gross_profit: Optional[FactValue] = None
    sga_expense: Optional[FactValue] = None
    depreciation_amortization: Optional[FactValue] = None
    operating_income: Optional[FactValue] = None
    net_income: Optional[FactValue] = None

    # Balance sheet (instant facts)
    total_assets: Optional[FactValue] = None
    current_assets: Optional[FactValue] = None
    receivables: Optional[FactValue] = None
    ppe_net: Optional[FactValue] = None
    total_liabilities: Optional[FactValue] = None
    current_liabilities: Optional[FactValue] = None
    long_term_debt: Optional[FactValue] = None
    retained_earnings: Optional[FactValue] = None
    total_equity: Optional[FactValue] = None
    shares_outstanding: Optional[FactValue] = None

    # Cash flow statement
    operating_cash_flow: Optional[FactValue] = None
    capex: Optional[FactValue] = None

    # Bookkeeping: which candidate tag was actually used for each metric,
    # so we can show it in the report / debug silently-wrong mappings.
    resolved_tags: dict = field(default_factory=dict)


@dataclass
class FilingTextSections:
    """Raw text of the sections we care about, keyed by SEC item label."""
    item_1a_risk_factors: Optional[str] = None   # Item 1A (10-K) / Part II Item 1A (10-Q)
    item_7_mdna: Optional[str] = None            # Item 7 (10-K) / Part I Item 2 (10-Q)
    item_9a_controls: Optional[str] = None       # Item 9A (10-K) / Part I Item 4 (10-Q)
    is_risk_factors_boilerplate: bool = False    # True if 10-Q used the
                                                  # "no material changes" clause


@dataclass
class Filing:
    """A single SEC filing, fully resolved: metadata + financials + text."""
    ticker: str
    cik: str                      # zero-padded 10-digit CIK, e.g. "0000320193"
    company_name: str
    form_type: FormType
    fiscal_year: int
    fiscal_period: str            # "FY", "Q1", "Q2", "Q3"
    filed_date: date
    accession_number: str
    period_end: date

    financials: Optional[FinancialPeriod] = None
    text_sections: Optional[FilingTextSections] = None
