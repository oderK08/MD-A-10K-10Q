import pytest

from equity_analyzer.data_layer.models import PeriodDuration
from equity_analyzer.redflags.altman_z import altman_z_score, classify_zone
from equity_analyzer.redflags.errors import InsufficientDataError, RedFlagError

from .factories import make_period

# A clean, "safe zone" annual period: current > liabilities, profitable,
# no debt overhang. Hand-computable so the formula itself is verified,
# not just "it runs".
HEALTHY_PERIOD_KWARGS = dict(
    total_assets=1_000_000,
    current_assets=600_000,
    current_liabilities=200_000,
    retained_earnings=300_000,
    operating_income=150_000,
    revenue=1_200_000,
    total_liabilities=400_000,
    total_equity=600_000,
)


def test_book_value_variant_used_when_no_market_value_given():
    period = make_period(duration=PeriodDuration.TWELVE_MONTH, **HEALTHY_PERIOD_KWARGS)
    result = altman_z_score(period)

    a = (600_000 - 200_000) / 1_000_000  # 0.4
    b = 300_000 / 1_000_000              # 0.3
    c = 150_000 / 1_000_000              # 0.15
    d = 600_000 / 400_000                # 1.5 (book equity / total liabilities)
    e = 1_200_000 / 1_000_000            # 1.2
    expected = 0.717 * a + 0.847 * b + 3.107 * c + 0.420 * d + 0.998 * e

    assert result.variant == "book_value"
    assert result.score == pytest.approx(expected)
    assert result.components == pytest.approx({"A": a, "B": b, "C": c, "D": d, "E": e})


def test_original_variant_used_when_market_value_given():
    period = make_period(duration=PeriodDuration.TWELVE_MONTH, **HEALTHY_PERIOD_KWARGS)
    result = altman_z_score(period, market_value_of_equity=2_000_000)

    a = (600_000 - 200_000) / 1_000_000
    b = 300_000 / 1_000_000
    c = 150_000 / 1_000_000
    d = 2_000_000 / 400_000  # market value / total liabilities, not book
    e = 1_200_000 / 1_000_000
    expected = 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e

    assert result.variant == "original"
    assert result.score == pytest.approx(expected)


def test_rejects_quarterly_period():
    period = make_period(duration=PeriodDuration.THREE_MONTH, **HEALTHY_PERIOD_KWARGS)
    with pytest.raises(RedFlagError, match="12-month period"):
        altman_z_score(period)


def test_missing_metric_raises_insufficient_data_error():
    kwargs = dict(HEALTHY_PERIOD_KWARGS)
    del kwargs["retained_earnings"]
    period = make_period(duration=PeriodDuration.TWELVE_MONTH, **kwargs)
    with pytest.raises(InsufficientDataError, match="retained_earnings"):
        altman_z_score(period)


def test_zone_classification_original_variant():
    assert classify_zone(3.5, "original") == "safe"
    assert classify_zone(2.99, "original") == "grey"  # boundary is exclusive on the safe side
    assert classify_zone(2.0, "original") == "grey"
    assert classify_zone(1.81, "original") == "grey"  # boundary is inclusive on the grey side
    assert classify_zone(1.0, "original") == "distress"


def test_zone_classification_book_value_variant():
    assert classify_zone(3.0, "book_value") == "safe"
    assert classify_zone(2.0, "book_value") == "grey"
    assert classify_zone(1.0, "book_value") == "distress"


def test_unknown_variant_rejected():
    with pytest.raises(ValueError, match="Unknown Altman"):
        classify_zone(2.0, "not_a_real_variant")
