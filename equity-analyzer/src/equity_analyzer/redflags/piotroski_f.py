"""
Piotroski F-Score -- 9 binary fundamental-strength signals (Piotroski,
2000), each worth 1 point (0-9 total). Like Beneish M, several of the 9
signals are year-over-year deltas, so this always takes a comparable
(current, prior) pair rather than a single period.

Profitability (4):
  1. positive_net_income        net_income > 0
  2. positive_cfo               operating_cash_flow > 0
  3. improving_roa              ROA(current) > ROA(prior)
  4. quality_of_earnings        operating_cash_flow > net_income (current)

Leverage / liquidity (3):
  5. decreasing_leverage        LT debt / assets fell YoY
  6. improving_current_ratio    current assets / current liabilities rose YoY
  7. no_new_shares              shares outstanding did not increase YoY

Operating efficiency (2):
  8. improving_gross_margin     gross profit / revenue rose YoY
  9. improving_asset_turnover   revenue / total assets rose YoY
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data_layer.models import FinancialPeriod
from .comparability import require_comparable_yoy
from .errors import InsufficientDataError, RedFlagError

MAX_SCORE = 9


@dataclass(frozen=True)
class PiotroskiFResult:
    score: int
    max_score: int
    criteria: dict  # name -> bool, one entry per signal above


def _value(period: FinancialPeriod, field_name: str, label: str) -> float:
    fact = getattr(period, field_name)
    if fact is None:
        raise InsufficientDataError(
            f"FinancialPeriod ({label}, accession {period.accession_number}) "
            f"is missing '{field_name}'; cannot compute Piotroski F-Score."
        )
    return fact.value


def _gross_profit(period: FinancialPeriod, label: str) -> float:
    if period.gross_profit is not None:
        return period.gross_profit.value
    return _value(period, "revenue", label) - _value(period, "cogs", label)


def piotroski_f_score(
    current: FinancialPeriod,
    prior: FinancialPeriod,
) -> PiotroskiFResult:
    require_comparable_yoy(current, prior)

    ni_c = _value(current, "net_income", "current")
    ni_p = _value(prior, "net_income", "prior")
    ta_c = _value(current, "total_assets", "current")
    ta_p = _value(prior, "total_assets", "prior")
    cfo_c = _value(current, "operating_cash_flow", "current")
    ltd_c = _value(current, "long_term_debt", "current")
    ltd_p = _value(prior, "long_term_debt", "prior")
    ca_c = _value(current, "current_assets", "current")
    cl_c = _value(current, "current_liabilities", "current")
    ca_p = _value(prior, "current_assets", "prior")
    cl_p = _value(prior, "current_liabilities", "prior")
    shares_c = _value(current, "shares_outstanding", "current")
    shares_p = _value(prior, "shares_outstanding", "prior")
    sales_c = _value(current, "revenue", "current")
    sales_p = _value(prior, "revenue", "prior")
    gp_c = _gross_profit(current, "current")
    gp_p = _gross_profit(prior, "prior")

    try:
        roa_c = ni_c / ta_c
        roa_p = ni_p / ta_p
        leverage_c = ltd_c / ta_c
        leverage_p = ltd_p / ta_p
        current_ratio_c = ca_c / cl_c
        current_ratio_p = ca_p / cl_p
        gross_margin_c = gp_c / sales_c
        gross_margin_p = gp_p / sales_p
        asset_turnover_c = sales_c / ta_c
        asset_turnover_p = sales_p / ta_p
    except ZeroDivisionError as exc:
        raise RedFlagError(
            f"Division by zero while computing Piotroski F-Score components "
            f"(current accession {current.accession_number} / prior "
            f"accession {prior.accession_number}): {exc}"
        ) from exc

    criteria = {
        "positive_net_income": ni_c > 0,
        "positive_cfo": cfo_c > 0,
        "improving_roa": roa_c > roa_p,
        "quality_of_earnings": cfo_c > ni_c,
        "decreasing_leverage": leverage_c < leverage_p,
        "improving_current_ratio": current_ratio_c > current_ratio_p,
        "no_new_shares": shares_c <= shares_p,
        "improving_gross_margin": gross_margin_c > gross_margin_p,
        "improving_asset_turnover": asset_turnover_c > asset_turnover_p,
    }

    return PiotroskiFResult(
        score=sum(criteria.values()),
        max_score=MAX_SCORE,
        criteria=criteria,
    )
