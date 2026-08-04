"""
Altman Z-Score -- bankruptcy / financial-distress risk from a single
period's balance sheet and income statement figures.

Two variants, and the choice between them is NOT cosmetic:

- ORIGINAL Z-Score (Altman, 1968), for publicly traded manufacturers:
      Z = 1.2*A + 1.4*B + 3.3*C + 0.6*D + 1.0*E
      D = Market Value of Equity / Total Liabilities
  This needs a live share price -- SEC XBRL `companyfacts` has no market
  price, only book figures. We never fabricate one: callers who want the
  original formula must pass `market_value_of_equity` explicitly (e.g.
  shares_outstanding * a quote from a market data provider).

- BOOK-VALUE Z'-Score (Altman's private-firm variant), used automatically
  when `market_value_of_equity` is not supplied:
      Z' = 0.717*A + 0.847*B + 3.107*C + 0.420*D' + 0.998*E
      D' = Book Value of Equity / Total Liabilities
  Different coefficients AND different distress thresholds -- not the
  same formula with a substituted input. `AltmanZResult.variant` records
  which one actually ran so a report can never blend the two silently.

Both variants require a full 12-month period. The E component (Sales /
Total Assets) and C component (EBIT proxy / Total Assets) are turnover
ratios calibrated on annual figures; feeding a raw quarter in would
understate them by roughly 4x with no annualization, so we reject
non-annual periods outright rather than produce a quietly wrong score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..data_layer.models import FinancialPeriod, PeriodDuration
from .errors import InsufficientDataError, RedFlagError

_ZONE_THRESHOLDS = {
    # variant -> (safe_cutoff, distress_cutoff); safe > cutoff > grey >= cutoff > distress
    "original": (2.99, 1.81),
    "book_value": (2.9, 1.23),
}


@dataclass(frozen=True)
class AltmanZResult:
    score: float
    variant: str  # "original" | "book_value"
    zone: str     # "safe" | "grey" | "distress"
    components: dict  # A, B, C, D, E raw ratios


def _value(period: FinancialPeriod, field_name: str) -> float:
    fact = getattr(period, field_name)
    if fact is None:
        raise InsufficientDataError(
            f"FinancialPeriod (accession {period.accession_number}) is "
            f"missing '{field_name}'; cannot compute Altman Z-Score."
        )
    return fact.value


def classify_zone(score: float, variant: str) -> str:
    """Maps a raw Altman score to its distress zone for the given variant."""
    try:
        safe_cutoff, distress_cutoff = _ZONE_THRESHOLDS[variant]
    except KeyError:
        raise ValueError(f"Unknown Altman Z-Score variant: {variant!r}") from None
    if score > safe_cutoff:
        return "safe"
    if score >= distress_cutoff:
        return "grey"
    return "distress"


def altman_z_score(
    period: FinancialPeriod,
    market_value_of_equity: Optional[float] = None,
) -> AltmanZResult:
    """
    Computes the Altman Z-Score (or Z'-Score if `market_value_of_equity`
    is omitted) for a single annual FinancialPeriod.
    """
    if period.duration != PeriodDuration.TWELVE_MONTH:
        raise RedFlagError(
            f"Altman Z-Score requires a full 12-month period -- its ratios "
            f"are calibrated annually, and applying them to a raw quarter "
            f"without annualizing would understate turnover ratios like "
            f"Sales/Total Assets by roughly 4x. Got "
            f"duration={period.duration.value!r} for accession "
            f"{period.accession_number}. Pass a 10-K's FinancialPeriod, or "
            f"build a trailing-twelve-month aggregate first."
        )

    total_assets = _value(period, "total_assets")
    current_assets = _value(period, "current_assets")
    current_liabilities = _value(period, "current_liabilities")
    retained_earnings = _value(period, "retained_earnings")
    operating_income = _value(period, "operating_income")
    revenue = _value(period, "revenue")
    total_liabilities = _value(period, "total_liabilities")

    try:
        a = (current_assets - current_liabilities) / total_assets
        b = retained_earnings / total_assets
        c = operating_income / total_assets
        e = revenue / total_assets

        if market_value_of_equity is not None:
            variant = "original"
            d = market_value_of_equity / total_liabilities
            score = 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e
        else:
            variant = "book_value"
            book_equity = _value(period, "total_equity")
            d = book_equity / total_liabilities
            score = 0.717 * a + 0.847 * b + 3.107 * c + 0.420 * d + 0.998 * e
    except ZeroDivisionError as exc:
        raise RedFlagError(
            f"Division by zero while computing Altman Z-Score ratios "
            f"(accession {period.accession_number}): {exc}"
        ) from exc

    return AltmanZResult(
        score=score,
        variant=variant,
        zone=classify_zone(score, variant),
        components={"A": a, "B": b, "C": c, "D": d, "E": e},
    )
