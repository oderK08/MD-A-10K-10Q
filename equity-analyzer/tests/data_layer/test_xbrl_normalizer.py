import json
from datetime import date
from pathlib import Path

import pytest

from equity_analyzer.data_layer.models import PeriodDuration
from equity_analyzer.data_layer.xbrl_normalizer import (
    build_financial_period,
    classify_duration,
)

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sample_companyfacts.json"


@pytest.fixture
def company_facts():
    return json.loads(FIXTURE_PATH.read_text())


def test_builds_period_for_original_filing(company_facts):
    period = build_financial_period(
        company_facts,
        accession_number="0000320193-24-000010",
        fiscal_year=2024,
        fiscal_period="Q1",
    )
    assert period.revenue.value == 1_000_000
    assert period.net_income.value == 120_000
    assert period.total_assets.value == 5_000_000
    assert period.period_end == date(2024, 3, 31)
    assert period.period_start == date(2024, 1, 1)
    assert period.duration == PeriodDuration.THREE_MONTH


def test_restatement_does_not_leak_into_original_filing(company_facts):
    """
    Critical rigor check: NetIncomeLoss for the 2025-Q1 period has TWO
    entries -- the original 10-Q value (90000, accn ...-000010) and a
    later restated value from a 10-Q/A (85000, accn ...-000045).
    When we build the FinancialPeriod for the ORIGINAL accession, we must
    get 90000, not 85000 -- otherwise a diff between two consecutive
    original filings would silently use a number that didn't exist yet
    at the time either filing was made.
    """
    period = build_financial_period(
        company_facts,
        accession_number="0000320193-25-000010",
        fiscal_year=2025,
        fiscal_period="Q1",
    )
    assert period.net_income.value == 90_000
    assert period.net_income.accession_number == "0000320193-25-000010"


def test_restated_value_is_reachable_via_its_own_accession(company_facts):
    period = build_financial_period(
        company_facts,
        accession_number="0000320193-25-000045",
        fiscal_year=2025,
        fiscal_period="Q1",
    )
    assert period.net_income.value == 85_000


def test_resolved_tags_are_tracked(company_facts):
    period = build_financial_period(
        company_facts,
        accession_number="0000320193-24-000010",
        fiscal_year=2024,
        fiscal_period="Q1",
    )
    assert period.resolved_tags["revenue"] == (
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    assert period.resolved_tags["net_income"] == "NetIncomeLoss"


def test_missing_metric_is_none_not_guessed(company_facts):
    period = build_financial_period(
        company_facts,
        accession_number="0000320193-24-000010",
        fiscal_year=2024,
        fiscal_period="Q1",
    )
    # sga_expense and long_term_debt are not present in the fixture at all
    assert period.sga_expense is None
    assert period.long_term_debt is None
    assert "sga_expense" not in period.resolved_tags


def test_classify_duration_instant():
    assert classify_duration(None, date(2024, 3, 31)) == PeriodDuration.INSTANT


def test_classify_duration_quarter():
    assert classify_duration(
        date(2024, 1, 1), date(2024, 3, 31)
    ) == PeriodDuration.THREE_MONTH


def test_classify_duration_year_to_date_six_month():
    assert classify_duration(
        date(2024, 1, 1), date(2024, 6, 30)
    ) == PeriodDuration.SIX_MONTH


def test_classify_duration_full_year():
    assert classify_duration(
        date(2024, 1, 1), date(2024, 12, 31)
    ) == PeriodDuration.TWELVE_MONTH


def test_missing_accession_raises_clear_error(company_facts):
    with pytest.raises(ValueError, match="No recognized financial facts"):
        build_financial_period(
            company_facts,
            accession_number="0000000000-00-000000",
            fiscal_year=2024,
            fiscal_period="Q1",
        )
