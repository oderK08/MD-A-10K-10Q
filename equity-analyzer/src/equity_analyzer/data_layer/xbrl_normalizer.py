"""
Normalizes raw SEC XBRL companyfacts data into FinancialPeriod objects.

Three rigor points handled explicitly here:

1. TAG VARIATION: the same economic concept is tagged differently across
   companies (e.g. Net Income might be `NetIncomeLoss` or `ProfitLoss`).
   We define an ordered list of candidate tags per metric and take the
   first one that has data -- and we RECORD which tag was used
   (`resolved_tags`) so a silent mismatch is auditable, not invisible.

2. RESTATEMENT SAFETY: XBRL's `units` arrays can contain multiple values
   for the same (start, end) period if a later filing restated it. We
   select the value whose `accn` (accession number) matches the specific
   filing we are building a FinancialPeriod for -- i.e. the value AS
   ORIGINALLY REPORTED in that filing -- unless the caller explicitly asks
   for the latest restated value.

3. PERIOD DURATION: for each fact we classify its duration
   (instant / 3M / 6M / 9M / 12M) from (start, end) rather than assuming
   based on form type, because 10-Qs commonly report both a quarter-alone
   and a year-to-date cumulative figure for the same concept.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .models import FactValue, FinancialPeriod, PeriodDuration


# Ordered candidate tags per normalized metric. First match wins.
# This list is not exhaustive of every possible GAAP tag in existence;
# it covers the common cases and is meant to be extended as edge cases
# are found in practice (that's why resolved_tags is tracked -- to make
# gaps visible instead of silently wrong).
CANDIDATE_TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "cogs": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
    ],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "total_assets": [
        "Assets",
    ],
    "current_assets": [
        "AssetsCurrent",
    ],
    "receivables": [
        "ReceivablesNetCurrent",
        "AccountsReceivableNetCurrent",
    ],
    "ppe_net": [
        "PropertyPlantAndEquipmentNet",
    ],
    "total_liabilities": [
        "Liabilities",
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ],
    "retained_earnings": [
        "RetainedEarningsAccumulatedDeficit",
    ],
    "total_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
}

# Concepts that are balance-sheet "instant" facts (a snapshot at period_end,
# no period_start). Everything else is a duration fact.
INSTANT_METRICS = {
    "total_assets", "current_assets", "receivables", "ppe_net",
    "total_liabilities", "current_liabilities", "long_term_debt",
    "retained_earnings", "total_equity", "shares_outstanding",
}


def classify_duration(period_start: Optional[date], period_end: date) -> PeriodDuration:
    if period_start is None:
        return PeriodDuration.INSTANT
    days = (period_end - period_start).days
    # Tolerant bucketing: real fiscal quarters vary +/- a few days.
    if days <= 100:
        return PeriodDuration.THREE_MONTH
    if days <= 190:
        return PeriodDuration.SIX_MONTH
    if days <= 280:
        return PeriodDuration.NINE_MONTH
    return PeriodDuration.TWELVE_MONTH


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _extract_fact_for_accession(
    company_facts: dict,
    metric: str,
    accession_number: str,
) -> Optional[FactValue]:
    """
    Search candidate tags (in order) for a value reported specifically
    in `accession_number`. Returns the first match found, tagged with
    which concept name resolved it.
    """
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    for tag in CANDIDATE_TAGS[metric]:
        concept_data = us_gaap.get(tag)
        if not concept_data:
            continue
        units = concept_data.get("units", {})
        for unit_name, entries in units.items():
            for entry in entries:
                if entry.get("accn") != accession_number:
                    continue
                period_end = _parse_date(entry["end"])
                period_start = (
                    _parse_date(entry["start"]) if "start" in entry else None
                )
                return FactValue(
                    concept=tag,
                    value=float(entry["val"]),
                    unit=unit_name,
                    period_start=period_start,
                    period_end=period_end,
                    duration=classify_duration(period_start, period_end),
                    accession_number=entry["accn"],
                    filed_date=_parse_date(entry["filed"]),
                    frame=entry.get("frame"),
                )
    return None


def build_financial_period(
    company_facts: dict,
    accession_number: str,
    fiscal_year: int,
    fiscal_period: str,
) -> FinancialPeriod:
    """
    Builds a FinancialPeriod by pulling every metric's value AS REPORTED
    in the specific filing identified by `accession_number`.

    If a metric has no value for this accession (company doesn't report
    it, or it uses a tag not in our candidate list), the corresponding
    field is left None rather than guessed -- callers (e.g. red-flag
    calculators) must handle missing data explicitly.
    """
    resolved: dict = {}
    values: dict = {}
    for metric in CANDIDATE_TAGS:
        fact = _extract_fact_for_accession(company_facts, metric, accession_number)
        values[metric] = fact
        if fact is not None:
            resolved[metric] = fact.concept

    # period_end / period_start / filed_date / duration for the period
    # object itself: derive from whichever duration-type fact we found
    # first (net_income is almost universally reported).
    reference_fact = values.get("net_income") or next(
        (v for v in values.values() if v is not None), None
    )
    if reference_fact is None:
        raise ValueError(
            f"No recognized financial facts found for accession "
            f"{accession_number}. The candidate tag list may need to be "
            f"extended for this filer."
        )

    return FinancialPeriod(
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        duration=reference_fact.duration,
        period_start=reference_fact.period_start,
        period_end=reference_fact.period_end,
        accession_number=accession_number,
        filed_date=reference_fact.filed_date,
        revenue=values["revenue"],
        cogs=values["cogs"],
        gross_profit=values["gross_profit"],
        sga_expense=values["sga_expense"],
        depreciation_amortization=values["depreciation_amortization"],
        operating_income=values["operating_income"],
        net_income=values["net_income"],
        total_assets=values["total_assets"],
        current_assets=values["current_assets"],
        receivables=values["receivables"],
        ppe_net=values["ppe_net"],
        total_liabilities=values["total_liabilities"],
        current_liabilities=values["current_liabilities"],
        long_term_debt=values["long_term_debt"],
        retained_earnings=values["retained_earnings"],
        total_equity=values["total_equity"],
        shares_outstanding=values["shares_outstanding"],
        operating_cash_flow=values["operating_cash_flow"],
        capex=values["capex"],
        resolved_tags=resolved,
    )
