import pytest

from equity_analyzer.data_layer.models import PeriodDuration
from equity_analyzer.redflags.beneish_m import beneish_m_score
from equity_analyzer.redflags.errors import IncomparablePeriodsError, InsufficientDataError

from .factories import make_period

# Every ratio except TATA is held at parity between current and prior
# (identical raw figures) so DSRI = GMI = AQI = SGI = DEPI = SGAI = LVGI = 1
# exactly -- this isolates and verifies the constant + coefficients + TATA
# term by hand, rather than trusting the formula against itself.
_STABLE_KWARGS = dict(
    receivables=200_000,
    revenue=1_000_000,
    cogs=600_000,
    gross_profit=400_000,
    current_assets=500_000,
    ppe_net=300_000,
    total_assets=1_000_000,
    depreciation_amortization=50_000,
    sga_expense=100_000,
    current_liabilities=200_000,
    long_term_debt=100_000,
)


def _period(fiscal_year, accession, **overrides):
    kwargs = dict(_STABLE_KWARGS)
    kwargs.update(overrides)
    return make_period(
        duration=PeriodDuration.TWELVE_MONTH,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        accession_number=accession,
        **kwargs,
    )


def test_neutral_indices_isolate_tata_term():
    current = _period(2024, "acc-current", net_income=100_000, operating_cash_flow=80_000)
    prior = _period(2023, "acc-prior", net_income=100_000, operating_cash_flow=80_000)

    result = beneish_m_score(current, prior)

    for key in ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI"):
        assert result.components[key] == pytest.approx(1.0), key

    tata = (100_000 - 80_000) / 1_000_000  # 0.02
    assert result.components["TATA"] == pytest.approx(tata)

    expected = (
        -4.84
        + 0.92 * 1 + 0.528 * 1 + 0.404 * 1 + 0.892 * 1
        + 0.115 * 1 - 0.172 * 1 + 4.679 * tata - 0.327 * 1
    )
    assert result.score == pytest.approx(expected)


def test_flagged_uses_threshold():
    current = _period(2024, "acc-current", net_income=100_000, operating_cash_flow=80_000)
    prior = _period(2023, "acc-prior", net_income=100_000, operating_cash_flow=80_000)

    lenient = beneish_m_score(current, prior, threshold=-100.0)
    strict = beneish_m_score(current, prior, threshold=100.0)

    assert lenient.flagged is True
    assert strict.flagged is False


def test_gross_profit_falls_back_to_revenue_minus_cogs():
    kwargs = dict(_STABLE_KWARGS)
    del kwargs["gross_profit"]
    current = make_period(
        duration=PeriodDuration.TWELVE_MONTH, fiscal_year=2024, fiscal_period="FY",
        accession_number="acc-current", net_income=100_000, operating_cash_flow=80_000, **kwargs,
    )
    prior = make_period(
        duration=PeriodDuration.TWELVE_MONTH, fiscal_year=2023, fiscal_period="FY",
        accession_number="acc-prior", net_income=100_000, operating_cash_flow=80_000, **kwargs,
    )
    result = beneish_m_score(current, prior)
    assert result.components["GMI"] == pytest.approx(1.0)


def test_missing_metric_raises_insufficient_data_error():
    current = _period(2024, "acc-current", net_income=100_000, operating_cash_flow=80_000)
    kwargs = dict(_STABLE_KWARGS)
    del kwargs["sga_expense"]
    prior = make_period(
        duration=PeriodDuration.TWELVE_MONTH, fiscal_year=2023, fiscal_period="FY",
        accession_number="acc-prior", net_income=100_000, operating_cash_flow=80_000, **kwargs,
    )
    with pytest.raises(InsufficientDataError, match="sga_expense"):
        beneish_m_score(current, prior)


def test_rejects_incomparable_periods():
    current = _period(2025, "acc-current", net_income=100_000, operating_cash_flow=80_000)
    current = make_period(
        duration=PeriodDuration.THREE_MONTH, fiscal_year=2025, fiscal_period="Q2",
        accession_number="acc-current", net_income=100_000, operating_cash_flow=80_000,
        **_STABLE_KWARGS,
    )
    prior = _period(2024, "acc-prior", net_income=100_000, operating_cash_flow=80_000)
    with pytest.raises(IncomparablePeriodsError):
        beneish_m_score(current, prior)
