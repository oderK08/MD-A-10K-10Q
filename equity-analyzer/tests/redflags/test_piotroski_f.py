import pytest

from equity_analyzer.data_layer.models import PeriodDuration
from equity_analyzer.redflags.errors import IncomparablePeriodsError, InsufficientDataError
from equity_analyzer.redflags.piotroski_f import piotroski_f_score

from .factories import make_period

PRIOR_KWARGS = dict(
    net_income=50_000,
    total_assets=1_000_000,
    long_term_debt=300_000,
    current_assets=400_000,
    current_liabilities=300_000,
    shares_outstanding=1_000_000,
    revenue=800_000,
    gross_profit=300_000,
)

# Every metric improved (or held flat, for shares) relative to PRIOR_KWARGS
# -- designed to pass all 9 criteria so the full scoring path is verified.
CURRENT_ALL_PASS_KWARGS = dict(
    net_income=80_000,
    total_assets=1_000_000,
    operating_cash_flow=100_000,
    long_term_debt=200_000,
    current_assets=500_000,
    current_liabilities=250_000,
    shares_outstanding=1_000_000,
    revenue=1_000_000,
    gross_profit=450_000,
)


def _current(**overrides):
    kwargs = dict(CURRENT_ALL_PASS_KWARGS)
    kwargs.update(overrides)
    return make_period(
        duration=PeriodDuration.TWELVE_MONTH, fiscal_year=2024, fiscal_period="FY",
        accession_number="acc-current", **kwargs,
    )


def _prior(**overrides):
    kwargs = dict(PRIOR_KWARGS)
    kwargs.update(overrides)
    return make_period(
        duration=PeriodDuration.TWELVE_MONTH, fiscal_year=2023, fiscal_period="FY",
        accession_number="acc-prior", **kwargs,
    )


def test_all_nine_criteria_pass():
    result = piotroski_f_score(_current(), _prior())
    assert result.score == 9
    assert result.max_score == 9
    assert all(result.criteria.values()), result.criteria


def test_net_loss_and_share_dilution_flip_two_criteria():
    current = _current(net_income=-10_000, shares_outstanding=1_200_000)
    result = piotroski_f_score(current, _prior())

    assert result.criteria["positive_net_income"] is False
    assert result.criteria["no_new_shares"] is False
    # quality_of_earnings (cfo > net_income) still holds: 100_000 > -10_000
    assert result.criteria["quality_of_earnings"] is True
    # a net loss also drags ROA below the prior year's positive ROA
    assert result.criteria["improving_roa"] is False
    assert result.score == 9 - 3


def test_worsening_leverage_and_liquidity_flip_criteria():
    current = _current(long_term_debt=500_000, current_assets=200_000)
    result = piotroski_f_score(current, _prior())

    assert result.criteria["decreasing_leverage"] is False
    assert result.criteria["improving_current_ratio"] is False


def test_missing_metric_raises_insufficient_data_error():
    prior = make_period(
        duration=PeriodDuration.TWELVE_MONTH, fiscal_year=2023, fiscal_period="FY",
        accession_number="acc-prior",
        **{k: v for k, v in PRIOR_KWARGS.items() if k != "long_term_debt"},
    )
    with pytest.raises(InsufficientDataError, match="long_term_debt"):
        piotroski_f_score(_current(), prior)


def test_rejects_incomparable_periods():
    current = make_period(
        duration=PeriodDuration.THREE_MONTH, fiscal_year=2024, fiscal_period="Q2",
        accession_number="acc-current", **CURRENT_ALL_PASS_KWARGS,
    )
    with pytest.raises(IncomparablePeriodsError):
        piotroski_f_score(current, _prior())
