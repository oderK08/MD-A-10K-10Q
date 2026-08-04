"""
Tests for the two non-candidate-list fallbacks added to xbrl_normalizer
after the multi-ticker real-SEC-API test surfaced structural gaps (see
that module's docstring, point 4):

- shares_outstanding falling back to the `dei` namespace
- total_liabilities falling back to a derived computation
  (LiabilitiesAndStockholdersEquity - StockholdersEquity)

Uses small hand-built companyfacts dicts (not the shared
sample_companyfacts.json fixture) so each test isolates exactly the
fallback path it's checking, without perturbing the existing fixture
other tests already depend on.
"""

from datetime import date

from equity_analyzer.data_layer.xbrl_normalizer import build_financial_period

ACCESSION = "0000000000-25-000001"


def _base_facts(**extra_us_gaap):
    """A minimal companyfacts dict with just enough for build_financial_period
    to succeed (a net_income fact as the reference), plus whatever extra
    us-gaap concepts a test adds."""
    facts = {
        "NetIncomeLoss": {
            "units": {"USD": [
                {"start": "2024-01-01", "end": "2024-12-31", "val": 100000,
                 "accn": ACCESSION, "filed": "2025-02-01"},
            ]},
        },
    }
    facts.update(extra_us_gaap)
    return {"facts": {"us-gaap": facts}}


def _instant(val):
    return {"units": {"USD": [
        {"end": "2024-12-31", "val": val, "accn": ACCESSION, "filed": "2025-02-01"},
    ]}}


def test_shares_outstanding_falls_back_to_dei_namespace():
    company_facts = _base_facts()
    company_facts["facts"]["dei"] = {
        "EntityCommonStockSharesOutstanding": {
            "units": {"shares": [
                {"end": "2024-12-31", "val": 7_000_000, "accn": ACCESSION, "filed": "2025-02-01"},
            ]},
        },
    }

    period = build_financial_period(company_facts, ACCESSION, fiscal_year=2024, fiscal_period="FY")

    assert period.shares_outstanding is not None
    assert period.shares_outstanding.value == 7_000_000
    assert period.resolved_tags["shares_outstanding"] == "dei:EntityCommonStockSharesOutstanding"


def test_shares_outstanding_prefers_us_gaap_over_dei_when_both_present():
    company_facts = _base_facts(CommonStockSharesOutstanding=_instant(6_500_000))
    company_facts["facts"]["dei"] = {
        "EntityCommonStockSharesOutstanding": {
            "units": {"shares": [
                {"end": "2024-12-31", "val": 7_000_000, "accn": ACCESSION, "filed": "2025-02-01"},
            ]},
        },
    }

    period = build_financial_period(company_facts, ACCESSION, fiscal_year=2024, fiscal_period="FY")

    assert period.shares_outstanding.value == 6_500_000
    assert period.resolved_tags["shares_outstanding"] == "CommonStockSharesOutstanding"


def test_shares_outstanding_none_when_neither_source_has_it():
    company_facts = _base_facts()
    period = build_financial_period(company_facts, ACCESSION, fiscal_year=2024, fiscal_period="FY")
    assert period.shares_outstanding is None
    assert "shares_outstanding" not in period.resolved_tags


def test_total_liabilities_derived_when_no_direct_tag_reported():
    """
    Reproduces a real balance-sheet presentation style (seen on Amazon's
    actual 10-K during the multi-ticker test) where only
    "Total liabilities and stockholders' equity" is reported -- never a
    standalone "Total liabilities" line under any tag name.
    """
    company_facts = _base_facts(
        LiabilitiesAndStockholdersEquity=_instant(9_000_000),
        StockholdersEquity=_instant(3_500_000),
    )

    period = build_financial_period(company_facts, ACCESSION, fiscal_year=2024, fiscal_period="FY")

    assert period.total_liabilities is not None
    assert period.total_liabilities.value == 9_000_000 - 3_500_000
    assert period.resolved_tags["total_liabilities"] == (
        "derived:LiabilitiesAndStockholdersEquity-StockholdersEquity"
    )


def test_total_liabilities_prefers_direct_tag_over_derived():
    company_facts = _base_facts(
        Liabilities=_instant(5_400_000),
        LiabilitiesAndStockholdersEquity=_instant(9_000_000),
        StockholdersEquity=_instant(3_500_000),
    )

    period = build_financial_period(company_facts, ACCESSION, fiscal_year=2024, fiscal_period="FY")

    # if it had used the derived path, this would be 9_000_000 - 3_500_000 = 5_500_000
    assert period.total_liabilities.value == 5_400_000
    assert period.resolved_tags["total_liabilities"] == "Liabilities"


def test_total_liabilities_not_derived_when_only_one_source_fact_present():
    company_facts = _base_facts(LiabilitiesAndStockholdersEquity=_instant(9_000_000))
    period = build_financial_period(company_facts, ACCESSION, fiscal_year=2024, fiscal_period="FY")
    assert period.total_liabilities is None


def test_total_liabilities_not_derived_when_periods_mismatch():
    company_facts = _base_facts(
        LiabilitiesAndStockholdersEquity={"units": {"USD": [
            {"end": "2024-12-31", "val": 9_000_000, "accn": ACCESSION, "filed": "2025-02-01"},
        ]}},
        StockholdersEquity={"units": {"USD": [
            # different period_end -- should NOT be combined
            {"end": "2024-09-30", "val": 3_500_000, "accn": ACCESSION, "filed": "2025-02-01"},
        ]}},
    )
    period = build_financial_period(company_facts, ACCESSION, fiscal_year=2024, fiscal_period="FY")
    assert period.total_liabilities is None
