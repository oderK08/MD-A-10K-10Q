"""
Normalizes raw SEC XBRL companyfacts data into FinancialPeriod objects.

Rigor points handled explicitly here:

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

4. NOT EVERY GAP IS A MISSING TAG -- some are structural, not fixable by
   adding a candidate name:
   - Financial institutions (banks, insurers) file an UNCLASSIFIED
     balance sheet under GAAP -- they genuinely never report
     `AssetsCurrent`/`LiabilitiesCurrent` at all, for any tag name,
     because "current vs non-current" isn't a meaningful split for a
     bank's balance sheet. Altman Z / Beneish M / Piotroski F are not
     considered meaningful for financial institutions in real-world
     practice either, for the same underlying reason -- this isn't a gap
     to fix, it's a scope boundary of these models.
   - Some filers never report a `Liabilities` subtotal at all -- their
     balance sheet goes straight from the liability line items to
     "Total liabilities and stockholders' equity" with no standalone
     total liabilities row. No alternate tag name exists for a number
     that was never reported as its own fact; `_derive_total_liabilities`
     computes it instead (LiabilitiesAndStockholdersEquity -
     StockholdersEquity) and marks the result as derived, not directly
     reported, in `resolved_tags`.
   - `shares_outstanding` is very often available ONLY as a cover-page
     fact in the `dei` (Document and Entity Information) taxonomy
     namespace (`dei:EntityCommonStockSharesOutstanding`), not under
     `us-gaap` at all -- `_extract_dei_fact_for_accession` checks that
     namespace as a fallback.
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
#
# Entries marked "(best-effort)" below were added from general knowledge
# of the US-GAAP taxonomy after a real multi-company test run surfaced
# the gap, WITHOUT being able to verify them against the live filing
# that actually had the gap (this project's dev sandboxes can't reach
# data.sec.gov -- see README). Treat them as informed guesses to
# validate against real data, not confirmed fixes.
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
        "CostOfServices",  # (best-effort) service-heavy filers (media, entertainment) sometimes report this instead of a goods-oriented COGS tag
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",  # (best-effort) filers that split "Selling" from "G&A" into separate line items -- this captures only the G&A component, not a true combined SG&A figure
    ],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
        "DepreciationAmortizationAndAccretionNet",  # (best-effort) seen on some cash-flow-statement presentations
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
        "NontradeReceivablesCurrent",  # (best-effort) filers with little/no trade AR (e.g. subscription businesses) sometimes only report this
        "AccountsAndOtherReceivablesNetCurrent",  # (best-effort)
    ],
    "ppe_net": [
        "PropertyPlantAndEquipmentNet",
        "PropertyPlantAndEquipmentNetExcludingOperatingLeaseRightOfUseAsset",  # (best-effort) common since the 2019 lease accounting standard (ASC 842) split PP&E from operating lease right-of-use assets for some filers
    ],
    "total_liabilities": [
        "Liabilities",
        # No reliable alternate tag NAME exists in the taxonomy for this
        # concept -- filers that don't report it at all (not under any
        # name) are handled by _derive_total_liabilities below, not by
        # adding more candidates here.
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",  # (best-effort)
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
        "CommonStockSharesIssued",  # (best-effort) only accurate if the filer holds no treasury stock; the dei: fallback below is the more reliable source for most large filers
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
}

# Cover-page fact (dei taxonomy, not us-gaap) that most large filers use
# for shares outstanding, often INSTEAD of a us-gaap balance-sheet tag.
DEI_SHARES_OUTSTANDING_TAG = "EntityCommonStockSharesOutstanding"

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


def _extract_single_tag(
    company_facts: dict,
    namespace: str,
    tag: str,
    accession_number: str,
) -> Optional[FactValue]:
    """
    Looks up ONE specific (namespace, tag) pair -- e.g. ("us-gaap",
    "NetIncomeLoss") or ("dei", "EntityCommonStockSharesOutstanding") --
    for the given accession. Shared by the candidate-list search below
    and by the dei/derived fallbacks, which each need a single named tag
    rather than "search this whole list".
    """
    facts_ns = company_facts.get("facts", {}).get(namespace, {})
    concept_data = facts_ns.get(tag)
    if not concept_data:
        return None
    units = concept_data.get("units", {})
    for unit_name, entries in units.items():
        for entry in entries:
            if entry.get("accn") != accession_number:
                continue
            period_end = _parse_date(entry["end"])
            period_start = (
                _parse_date(entry["start"]) if "start" in entry else None
            )
            concept_label = tag if namespace == "us-gaap" else f"{namespace}:{tag}"
            return FactValue(
                concept=concept_label,
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
    for tag in CANDIDATE_TAGS[metric]:
        fact = _extract_single_tag(company_facts, "us-gaap", tag, accession_number)
        if fact is not None:
            return fact
    return None


def _extract_dei_fact_for_accession(
    company_facts: dict,
    tag: str,
    accession_number: str,
) -> Optional[FactValue]:
    """dei-namespace fallback -- see module docstring point 4."""
    return _extract_single_tag(company_facts, "dei", tag, accession_number)


def _derive_total_liabilities(
    company_facts: dict,
    accession_number: str,
) -> Optional[FactValue]:
    """
    Derives total liabilities as LiabilitiesAndStockholdersEquity minus
    StockholdersEquity, for filers that never report a standalone
    `Liabilities` fact under any tag name. See module docstring point 4.
    Only used if both source facts exist for the SAME accession and the
    SAME period_end -- otherwise we'd silently mix mismatched periods.
    """
    total = _extract_single_tag(
        company_facts, "us-gaap", "LiabilitiesAndStockholdersEquity", accession_number
    )
    equity = _extract_single_tag(
        company_facts, "us-gaap", "StockholdersEquity", accession_number
    )
    if total is None or equity is None or total.period_end != equity.period_end:
        return None
    return FactValue(
        concept="derived:LiabilitiesAndStockholdersEquity-StockholdersEquity",
        value=total.value - equity.value,
        unit=total.unit,
        period_start=total.period_start,
        period_end=total.period_end,
        duration=total.duration,
        accession_number=accession_number,
        filed_date=total.filed_date,
        frame=None,
    )


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
    calculators) must handle missing data explicitly. Two metrics get an
    extra fallback beyond the plain candidate-tag search (dei namespace
    for shares_outstanding, a derived computation for total_liabilities)
    -- see the module docstring for why those two specifically need one.
    """
    resolved: dict = {}
    values: dict = {}
    for metric in CANDIDATE_TAGS:
        fact = _extract_fact_for_accession(company_facts, metric, accession_number)
        values[metric] = fact
        if fact is not None:
            resolved[metric] = fact.concept

    if values["shares_outstanding"] is None:
        dei_fact = _extract_dei_fact_for_accession(
            company_facts, DEI_SHARES_OUTSTANDING_TAG, accession_number
        )
        if dei_fact is not None:
            values["shares_outstanding"] = dei_fact
            resolved["shares_outstanding"] = dei_fact.concept

    if values["total_liabilities"] is None:
        derived_fact = _derive_total_liabilities(company_facts, accession_number)
        if derived_fact is not None:
            values["total_liabilities"] = derived_fact
            resolved["total_liabilities"] = derived_fact.concept

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
