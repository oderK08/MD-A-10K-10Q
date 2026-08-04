"""
Shared guard for year-over-year red-flag comparisons.

Rationale (agreed before coding this module):
- Quantitative red flags (Beneish M, Piotroski F) are calibrated on annual
  data. Applying them to two CONSECUTIVE quarters conflates seasonality
  with a real signal (e.g. sales growth from Q4 to Q1 can be wildly
  negative for a retailer every single year, manipulator or not).
- The fix is to always compare the SAME fiscal quarter a year apart
  (Q2 2025 vs Q2 2024), or two full fiscal years, never two arbitrary
  periods -- and to make that a hard requirement, not a note in a
  docstring nobody reads before calling the function wrong.
- Mixing a period's duration (e.g. a Q2 "3 months ended" figure against
  a "6 months ended" year-to-date figure for what the caller thinks is
  the same quarter) is an even more dangerous, silent error, so duration
  equality is checked first.
"""

from __future__ import annotations

from ..data_layer.models import FinancialPeriod, PeriodDuration
from .errors import IncomparablePeriodsError


def require_comparable_yoy(current: FinancialPeriod, prior: FinancialPeriod) -> None:
    """
    Raises IncomparablePeriodsError unless `current` and `prior` form a
    valid year-over-year pair:
      - same `duration` (both instant-derived durations must match exactly,
        e.g. both THREE_MONTH, or both TWELVE_MONTH)
      - `current.fiscal_year` strictly greater than `prior.fiscal_year`
      - if the duration isn't a full fiscal year, `fiscal_period` must
        match too (Q2 vs Q2, not Q2 vs Q1)
    """
    if current.duration != prior.duration:
        raise IncomparablePeriodsError(
            f"Cannot compare periods of different duration: "
            f"current={current.duration.value!r} (accession "
            f"{current.accession_number}) vs prior={prior.duration.value!r} "
            f"(accession {prior.accession_number}). This usually means one "
            f"period is a single quarter and the other a year-to-date "
            f"cumulative figure -- comparing them directly produces a "
            f"meaningless ratio, not a real signal."
        )

    if current.fiscal_year <= prior.fiscal_year:
        raise IncomparablePeriodsError(
            f"'current' (FY{current.fiscal_year} {current.fiscal_period}) "
            f"must be a strictly later fiscal year than 'prior' "
            f"(FY{prior.fiscal_year} {prior.fiscal_period}) for a "
            f"year-over-year red-flag comparison."
        )

    if current.duration != PeriodDuration.TWELVE_MONTH:
        if current.fiscal_period != prior.fiscal_period:
            raise IncomparablePeriodsError(
                f"Quarterly red-flag comparisons must use the SAME fiscal "
                f"quarter a year apart to neutralize seasonality (e.g. Q2 "
                f"vs Q2), not two different quarters. Got "
                f"current={current.fiscal_period!r} vs "
                f"prior={prior.fiscal_period!r}."
            )
