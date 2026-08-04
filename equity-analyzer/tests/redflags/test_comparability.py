import pytest

from equity_analyzer.data_layer.models import PeriodDuration
from equity_analyzer.redflags.comparability import require_comparable_yoy
from equity_analyzer.redflags.errors import IncomparablePeriodsError

from .factories import make_period


def test_allows_two_full_fiscal_years():
    current = make_period(fiscal_year=2024, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH)
    prior = make_period(fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH)
    require_comparable_yoy(current, prior)  # must not raise


def test_allows_same_quarter_year_apart():
    current = make_period(fiscal_year=2025, fiscal_period="Q2", duration=PeriodDuration.THREE_MONTH)
    prior = make_period(fiscal_year=2024, fiscal_period="Q2", duration=PeriodDuration.THREE_MONTH)
    require_comparable_yoy(current, prior)  # must not raise


def test_rejects_mismatched_duration():
    """Quarter-alone vs year-to-date cumulative is the classic silent trap."""
    current = make_period(duration=PeriodDuration.THREE_MONTH, fiscal_year=2025, fiscal_period="Q2")
    prior = make_period(duration=PeriodDuration.SIX_MONTH, fiscal_year=2024, fiscal_period="Q2")
    with pytest.raises(IncomparablePeriodsError, match="different duration"):
        require_comparable_yoy(current, prior)


def test_rejects_consecutive_quarters_instead_of_yoy():
    """Comparing Q2 to a different quarter a year apart is seasonality, not signal."""
    current = make_period(duration=PeriodDuration.THREE_MONTH, fiscal_year=2025, fiscal_period="Q2")
    prior = make_period(duration=PeriodDuration.THREE_MONTH, fiscal_year=2024, fiscal_period="Q1")
    with pytest.raises(IncomparablePeriodsError, match="SAME fiscal quarter"):
        require_comparable_yoy(current, prior)


def test_rejects_current_not_later_than_prior():
    current = make_period(fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH)
    prior = make_period(fiscal_year=2024, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH)
    with pytest.raises(IncomparablePeriodsError, match="strictly later"):
        require_comparable_yoy(current, prior)


def test_rejects_equal_fiscal_years():
    current = make_period(fiscal_year=2024, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH)
    prior = make_period(fiscal_year=2024, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH)
    with pytest.raises(IncomparablePeriodsError, match="strictly later"):
        require_comparable_yoy(current, prior)
