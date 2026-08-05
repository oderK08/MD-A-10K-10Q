"""
Tests for the opt-in AI summary module (report/ai_summary.py).

Consistent with every other network-touching client in this project
(EdgarClient's actual HTTP methods have no pytest coverage either --
only pure logic like filing_index_url does): the real Claude API call
is NOT exercised here, to keep `pytest` itself offline and deterministic.
It WAS validated manually against the real api.anthropic.com during
development (a deliberately-invalid key correctly produces a parsed,
readable `unavailable_reason` from the real 401 response) -- see the
module's commit message for that verification, not a test in this file.
"""

from datetime import date

import pytest

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.report.ai_summary import attach_ai_summary, build_prompt_context
from equity_analyzer.report.report_data import build_report_data
from equity_analyzer.sentiment.lm_dictionary import LMDictionary

from .factories import make_filing, make_financial_period

_METRICS = dict(
    revenue=1_200_000, cogs=700_000, gross_profit=500_000, sga_expense=100_000,
    depreciation_amortization=50_000, operating_income=150_000, net_income=100_000,
    total_assets=1_000_000, current_assets=600_000, receivables=150_000,
    ppe_net=300_000, total_liabilities=400_000, current_liabilities=200_000,
    long_term_debt=100_000, retained_earnings=300_000, total_equity=600_000,
    shares_outstanding=1_000_000, operating_cash_flow=120_000,
)

DICTIONARY = LMDictionary(
    words_by_category={
        "negative": frozenset({"decline"}),
        "positive": frozenset({"growth"}),
        "uncertainty": frozenset(), "litigious": frozenset(),
        "strong_modal": frozenset(), "weak_modal": frozenset(), "constraining": frozenset(),
    }
)


def _filing(accession, year, rf_text, mdna_text):
    financials = make_financial_period(
        fiscal_year=year, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
        accession_number=accession, period_end=date(year, 12, 31), **_METRICS,
    )
    text_sections = FilingTextSections(
        item_1a_risk_factors=rf_text, item_7_mdna=mdna_text, is_risk_factors_boilerplate=False,
    )
    return make_filing(
        company_name="Acme Corp", ticker="ACME", cik="0000320193",
        fiscal_year=year, fiscal_period="FY", form_type=FormType.TEN_K,
        accession_number=accession, period_end=date(year, 12, 31),
        financials=financials, text_sections=text_sections,
    )


def _report():
    prior_rf = (
        "Risks Related to Demand\nOur revenue could decline due to competition.\n\n"
        "Risks Related to Litigation\nWe are involved in ongoing litigation."
    )
    current_rf = (
        "Risks Related to Demand\nOur revenue could decline due to intense global competition.\n\n"
        "Risks Related to Cybersecurity\nA data breach could disrupt our operations."
    )
    current = _filing("acc-2024", 2024, current_rf, "revenue grew due to strong growth this year")
    prior = _filing("acc-2023", 2023, prior_rf, "revenue was flat last year")
    return build_report_data(current, prior, DICTIONARY)


def test_build_prompt_context_includes_red_flags_and_diff_groups():
    context = build_prompt_context(_report())
    assert "Acme Corp" in context
    assert "Altman Z-Score" in context
    assert "Piotroski F-Score" in context
    # a matched, changed sub-theme
    assert "Risks Related to Demand" in context
    assert "modifiee" in context
    # a genuinely new sub-theme (only in current year)
    assert "Risks Related to Cybersecurity" in context
    assert "nouvelle sous-thematique" in context
    # a genuinely removed sub-theme (only in prior year)
    assert "Risks Related to Litigation" in context
    assert "sous-thematique supprimee" in context
    # a real excerpt, not just counts -- grounding the model in actual text
    assert "data breach" in context


def test_build_prompt_context_never_mentions_unavailable_sections():
    """A report with no prior filing has no diff/Beneish/Piotroski --
    the prompt must not reference sections that weren't computed."""
    current = _filing("acc-2024", 2024, "some risk text", "some mdna text")
    report = build_report_data(current, None, DICTIONARY)
    context = build_prompt_context(report)
    assert "Beneish" not in context
    assert "Piotroski" not in context


def test_attach_ai_summary_without_api_key_is_unavailable_and_makes_no_network_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = _report()

    result = attach_ai_summary(report, api_key=None)

    assert result.ai_summary is not None
    assert not result.ai_summary.available
    assert "ANTHROPIC_API_KEY" in result.ai_summary.unavailable_reason
    # the rest of the report is untouched
    assert result.filing is report.filing
    assert report.ai_summary is None  # original object never mutated


def test_attach_ai_summary_surfaces_a_non_200_response_as_a_readable_reason(monkeypatch):
    """
    The real 401 shape (`{"type":"error","error":{"type":"authentication_error",
    "message":"invalid x-api-key"}}`), captured verbatim from a real call to
    api.anthropic.com with a deliberately-invalid key during development,
    used here as a fixture so the parsing path is tested without making
    pytest itself depend on live network.
    """
    class _FakeResponse:
        status_code = 401
        text = '{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'

    def _fake_post(*args, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr("equity_analyzer.report.ai_summary.requests.post", _fake_post)

    result = attach_ai_summary(_report(), api_key="sk-ant-fake-key")

    assert not result.ai_summary.available
    assert "401" in result.ai_summary.unavailable_reason
    assert "authentication_error" in result.ai_summary.unavailable_reason


def test_attach_ai_summary_succeeds_with_a_well_formed_response(monkeypatch):
    class _FakeResponse:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "Synthese factuelle du filing."}]}

    def _fake_post(*args, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr("equity_analyzer.report.ai_summary.requests.post", _fake_post)

    result = attach_ai_summary(_report(), api_key="sk-ant-fake-key")

    assert result.ai_summary.available
    assert result.ai_summary.value["text"] == "Synthese factuelle du filing."
