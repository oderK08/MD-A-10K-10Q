"""
Beneish M-Score -- likelihood of earnings manipulation, from the 8-variable
model (Beneish, 1999). Requires a year-over-year pair of periods; every
one of the 8 indices is itself a ratio-of-ratios between `current` and
`prior`, so there is no single-period version of this score.

    M = -4.84
        + 0.92 * DSRI    (Days' Sales in Receivables Index)
        + 0.528 * GMI    (Gross Margin Index)
        + 0.404 * AQI    (Asset Quality Index)
        + 0.892 * SGI    (Sales Growth Index)
        + 0.115 * DEPI   (Depreciation Index)
        - 0.172 * SGAI   (SG&A Index)
        + 4.679 * TATA   (Total Accruals to Total Assets)
        - 0.327 * LVGI   (Leverage Index)

Approximations, made explicit rather than silently assumed:
- The model's "Income from Continuing Operations" input for TATA is
  approximated with `net_income`, since Module 1's candidate-tag list
  does not currently isolate continuing-operations income separately.
- `gross_profit` falls back to `revenue - cogs` when a filer doesn't tag
  GrossProfit directly (some don't).

A commonly cited cutoff is M > -1.78 => elevated risk of manipulation
(Beneish, 1999); some practitioners use -2.22. Threshold is a parameter,
not hardcoded, so callers can apply the convention they trust.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data_layer.models import FinancialPeriod
from .comparability import require_comparable_yoy
from .errors import InsufficientDataError, RedFlagError

DEFAULT_THRESHOLD = -1.78


@dataclass(frozen=True)
class BeneishMResult:
    score: float
    flagged: bool
    threshold: float
    components: dict  # DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI, TATA


def _value(period: FinancialPeriod, field_name: str, label: str) -> float:
    fact = getattr(period, field_name)
    if fact is None:
        raise InsufficientDataError(
            f"FinancialPeriod ({label}, accession {period.accession_number}) "
            f"is missing '{field_name}'; cannot compute Beneish M-Score."
        )
    return fact.value


def _gross_profit(period: FinancialPeriod, label: str) -> float:
    if period.gross_profit is not None:
        return period.gross_profit.value
    return _value(period, "revenue", label) - _value(period, "cogs", label)


def beneish_m_score(
    current: FinancialPeriod,
    prior: FinancialPeriod,
    threshold: float = DEFAULT_THRESHOLD,
) -> BeneishMResult:
    require_comparable_yoy(current, prior)

    rec_c = _value(current, "receivables", "current")
    sales_c = _value(current, "revenue", "current")
    ca_c = _value(current, "current_assets", "current")
    ppe_c = _value(current, "ppe_net", "current")
    ta_c = _value(current, "total_assets", "current")
    dep_c = _value(current, "depreciation_amortization", "current")
    sga_c = _value(current, "sga_expense", "current")
    cl_c = _value(current, "current_liabilities", "current")
    ltd_c = _value(current, "long_term_debt", "current")
    ni_c = _value(current, "net_income", "current")
    cfo_c = _value(current, "operating_cash_flow", "current")
    gp_c = _gross_profit(current, "current")

    rec_p = _value(prior, "receivables", "prior")
    sales_p = _value(prior, "revenue", "prior")
    ca_p = _value(prior, "current_assets", "prior")
    ppe_p = _value(prior, "ppe_net", "prior")
    ta_p = _value(prior, "total_assets", "prior")
    dep_p = _value(prior, "depreciation_amortization", "prior")
    sga_p = _value(prior, "sga_expense", "prior")
    cl_p = _value(prior, "current_liabilities", "prior")
    ltd_p = _value(prior, "long_term_debt", "prior")
    gp_p = _gross_profit(prior, "prior")

    try:
        dsri = (rec_c / sales_c) / (rec_p / sales_p)
        gmi = (gp_p / sales_p) / (gp_c / sales_c)
        aqi = (1 - (ca_c + ppe_c) / ta_c) / (1 - (ca_p + ppe_p) / ta_p)
        sgi = sales_c / sales_p
        depi = (dep_p / (dep_p + ppe_p)) / (dep_c / (dep_c + ppe_c))
        sgai = (sga_c / sales_c) / (sga_p / sales_p)
        lvgi = ((cl_c + ltd_c) / ta_c) / ((cl_p + ltd_p) / ta_p)
        tata = (ni_c - cfo_c) / ta_c
    except ZeroDivisionError as exc:
        raise RedFlagError(
            f"Division by zero while computing Beneish M-Score components "
            f"(current accession {current.accession_number} / prior "
            f"accession {prior.accession_number}): {exc}"
        ) from exc

    score = (
        -4.84
        + 0.92 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.679 * tata
        - 0.327 * lvgi
    )

    return BeneishMResult(
        score=score,
        flagged=score > threshold,
        threshold=threshold,
        components={
            "DSRI": dsri,
            "GMI": gmi,
            "AQI": aqi,
            "SGI": sgi,
            "DEPI": depi,
            "SGAI": sgai,
            "LVGI": lvgi,
            "TATA": tata,
        },
    )
