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
from equity_analyzer.report.ai_summary import _SYSTEM_PROMPT, attach_ai_summary, build_prompt_context
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


def test_build_prompt_context_sends_the_themes_not_the_computed_ratios():
    """
    The summary must come from READING the selected sub-themes, not from
    restating indicators the reader already has on page 2 (explicit user
    request). Guaranteed by withholding them rather than by instructing
    the model to ignore them: it cannot lean on what it never receives.
    """
    context = build_prompt_context(_report())
    assert "Acme Corp" in context
    for indicator in ("Altman", "Beneish", "Piotroski", "Tonalite", "Loughran"):
        assert indicator not in context, f"{indicator} must not reach the summary prompt"
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


def test_build_prompt_context_sends_real_mdna_sentences_not_just_word_counts():
    """
    Regression test for the bug that made the AI summary generic on a
    real NVDA report: the MD&A -- where concrete guidance actually lives
    (pricing, margins, demand) -- used to be sent as word counts ONLY,
    with zero text. The prompt asks the model to quote the most
    meaningful changed sentences verbatim as evidence for its verdict,
    which is impossible for a section it never receives. Uses the
    user's own worked example (a disclosed DRAM price increase) as the
    sentence that must survive into the prompt intact.
    """
    prior_mdna = (
        "We expect DRAM prices to remain under pressure during fiscal 2025.\n\n"
        "We may be unable to obtain sufficient wafer supply."
    )
    current_mdna = (
        "We expect DRAM prices to increase approximately 5% during fiscal 2026.\n\n"
        "We may be unable to obtain sufficient wafer supply."
    )
    current = _filing("acc-2024", 2024, "Risks Related to Demand\nDemand could decline.", current_mdna)
    prior = _filing("acc-2023", 2023, "Risks Related to Demand\nDemand could decline.", prior_mdna)
    context = build_prompt_context(build_report_data(current, prior, DICTIONARY))

    # the full sentence, verbatim and untruncated -- quotable as-is
    assert "We expect DRAM prices to increase approximately 5% during fiscal 2026." in context
    # and the prior wording it replaced, so the model can weigh the shift
    assert "We expect DRAM prices to remain under pressure during fiscal 2025." in context
    # the genuinely unchanged sentence must NOT be presented as a change
    assert "wafer supply" not in context


def test_build_prompt_context_labels_removed_sentences_as_removals():
    """
    A company dropping a risk it disclosed last year is a signal in its
    own right, and the prompt tells the model to weigh it as one -- so
    removals must reach it explicitly labeled, not silently omitted or
    lumped in with additions.
    """
    prior_rf = (
        "Risks Related to Demand\nOur revenue could decline due to competition.\n\n"
        "We face a material risk of prolonged customer concentration."
    )
    current_rf = "Risks Related to Demand\nOur revenue could decline due to competition."
    current = _filing("acc-2024", 2024, current_rf, "mdna text")
    prior = _filing("acc-2023", 2023, prior_rf, "mdna text")
    context = build_prompt_context(build_report_data(current, prior, DICTIONARY))

    assert "SUPPRIME" in context
    assert "prolonged customer concentration" in context


def test_build_prompt_context_prioritizes_quantified_sentences_when_budget_is_tight():
    """
    The excerpt budget is capped, so ranking decides what the model
    actually gets to see. A sentence carrying a concrete number (the
    shape of the user's DRAM example) must outrank unquantified
    boilerplate rather than losing to it on document order.
    """
    from equity_analyzer.report.ai_summary import _MAX_EXCERPTS_PER_SECTION, _segment_excerpt_lines
    from equity_analyzer.diff.text_diff import DiffSegment

    boilerplate = [
        DiffSegment(kind="added", text=f"We may be unable to execute our strategy in region {i}.")
        for i in range(_MAX_EXCERPTS_PER_SECTION + 5)
    ]
    quantified = DiffSegment(
        kind="added", text="We expect average selling prices to increase approximately 5%."
    )
    # deliberately last in document order -- it must still make the cut
    lines = _segment_excerpt_lines(boilerplate + [quantified], _MAX_EXCERPTS_PER_SECTION)

    assert len(lines) == _MAX_EXCERPTS_PER_SECTION
    assert any("increase approximately 5%" in line for line in lines)


def test_quantified_fact_detection_ignores_years_and_index_numbers():
    """
    The excerpt ranking treats a quantified magnitude as more
    decision-relevant than boilerplate. The first version matched any
    digit at all, which ranked badly on real filing text: years,
    section numbers and list indices are digits carrying no magnitude,
    so everything tied and the length tiebreak handed the budget to the
    longest boilerplate (caught by
    test_build_prompt_context_prioritizes_quantified_sentences_when_budget_is_tight).
    """
    from equity_analyzer.report.ai_summary import _QUANTIFIED_FACT_RE as pattern

    for quantified in [
        "We expect DRAM prices to increase approximately 5%.",
        "Revenue increased 12 percent year over year.",
        "Gross margin improved by 150 basis points.",
        "We recorded a charge of $450 million.",
    ]:
        assert pattern.search(quantified), quantified

    for not_quantified in [
        "During fiscal 2026 we continued operations.",
        "See Item 1A for details.",
        "We may be unable to execute our strategy in region 3.",
        "Reduced demand could adversely affect our results.",
    ]:
        assert not pattern.search(not_quantified), not_quantified


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


def test_system_prompt_asks_for_a_bullish_bearish_verdict_with_verbatim_evidence():
    """
    Locked-in per explicit user request after reading a real generated
    NVDA report: the point of this section is a CONCRETE directional
    read of the changes (bullish vs bearish), backed by the 2-3 most
    important real sentences quoted verbatim -- not a neutral
    restatement of counts and sentiment scores, which is what the
    previous framing produced.
    """
    prompt = _SYSTEM_PROMPT.lower()
    assert "bullish" in prompt and "bearish" in prompt
    assert "verdict" in prompt
    assert "verbatim" in prompt


def test_system_prompt_still_forbids_buy_sell_advice_and_external_company_knowledge():
    """
    The two guardrails that did NOT move when the directional-verdict
    framing replaced the strict-factual one -- asserted separately from
    the verdict test above so that loosening one can never silently
    loosen the other:

    - a bullish/bearish READ of the filing's changes is now requested,
      but a buy/sell recommendation or price target still never is;
    - the model still may not import facts about THIS company from
      training data (general economic reasoning from the provided facts
      is what's wanted instead).
    """
    prompt = _SYSTEM_PROMPT.lower()
    # Matched on the concepts, not one exact phrasing: the prompt gets
    # reworded often and an over-literal assertion fails on a rewrite
    # that kept the guardrail perfectly intact (which is what happened).
    assert "achat" in prompt and "vente" in prompt
    assert "objectif de cours" in prompt
    assert "memoire d'entrainement" in prompt
    assert "invente pas" in prompt
