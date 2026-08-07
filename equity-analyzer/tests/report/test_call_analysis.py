"""
The reading of the call: what goes into the prompt, and what the prompt
is required to say.

The prompt tests are not decoration. Everything that makes this output
trustworthy -- that it quotes verbatim, that it separates fact from
inference, that it refuses to invent a consensus it was not given -- is
an instruction, not a code path, so an instruction quietly dropped in a
future edit is a silent regression. Each property gets its own test, so
that relaxing one cannot relax another by accident.
"""

from __future__ import annotations

from datetime import date

import pytest

from equity_analyzer.data_layer.earnings_expectations import QuarterExpectation
from equity_analyzer.report import call_analysis
from equity_analyzer.report.call_analysis import (
    TARGET_WORDS_HIGH,
    TARGET_WORDS_LOW,
    _SYSTEM_PROMPT,
    analyse_call,
    build_prompt,
    expectations_block,
)
from equity_analyzer.report.claude_client import ClaudeError

TRANSCRIPT = (
    "Operator\nWelcome to the fourth quarter earnings call.\n"
    "Chun Lin Hsieh -- CEO\nWe expect average selling prices to increase approximately 5%.\n"
)


def _expectation(estimated=1.00, reported=1.19, pct=19.0, ending=date(2025, 12, 31)):
    return QuarterExpectation(
        fiscal_date_ending=ending,
        reported_date=date(2026, 2, 3),
        estimated_eps=estimated,
        reported_eps=reported,
        surprise=None if reported is None or estimated is None else reported - estimated,
        surprise_pct=pct,
    )


# -- What the model receives -------------------------------------------


def test_the_transcript_goes_in_whole():
    """
    Excerpting it would mean choosing what matters before the model has
    read it, which is the judgement being delegated.
    """
    prompt = build_prompt("AAOI", "2026Q1", TRANSCRIPT)
    assert TRANSCRIPT in prompt


def test_the_expectations_come_before_the_transcript():
    """
    The model should read the call already knowing what it has to be
    measured against, rather than forming a view and then checking it.
    """
    prompt = build_prompt("AAOI", "2026Q1", TRANSCRIPT, expectation=_expectation())
    assert prompt.index("ATTENTES DU MARCHE") < prompt.index("DEBUT DU TRANSCRIPT")


def test_the_consensus_and_the_reported_figure_both_reach_the_prompt():
    block = expectations_block(_expectation(estimated=1.00, reported=1.19, pct=19.0))
    assert "1.00" in block
    assert "1.19" in block
    assert "+19.0%" in block


def test_the_beat_history_travels_with_the_current_quarter():
    """
    A company that has beaten by two cents every quarter for two years
    and beats by two cents again has met expectations, not exceeded
    them. Without the record, the model cannot tell those apart.
    """
    history = [
        _expectation(pct=2.1, ending=date(2025, 9, 30)),
        _expectation(pct=1.9, ending=date(2025, 6, 30)),
    ]
    block = expectations_block(_expectation(pct=2.0), history)

    assert "PALMARES" in block
    assert "2025-09-30" in block
    assert "2025-06-30" in block


def test_absent_expectations_are_stated_explicitly_never_just_omitted():
    """
    THE failure this guards. A model handed a transcript with no mention
    of expectations does not conclude that expectations are unknown, it
    supplies them from whatever it remembers about the company, and the
    resulting sentence ("slightly below what the street was looking
    for") is indistinguishable from a grounded one.
    """
    block = expectations_block(None)
    assert "NON DISPONIBLES" in block
    assert "n'avance aucun chiffre attendu" in block

    prompt = build_prompt("AAOI", "2026Q1", TRANSCRIPT, expectation=None)
    assert "NON DISPONIBLES" in prompt


def test_a_quarter_with_no_published_estimate_does_not_print_a_fake_one():
    block = expectations_block(_expectation(estimated=None, reported=1.19, pct=None))
    assert "non publie" in block


# -- What the prompt is required to say --------------------------------


def test_system_prompt_requires_verbatim_quotes():
    assert "mot pour mot" in _SYSTEM_PROMPT
    assert "guillemets" in _SYSTEM_PROMPT


def test_system_prompt_requires_separating_what_was_said_from_what_is_inferred():
    assert "DIT" in _SYSTEM_PROMPT and "DEDUIS" in _SYSTEM_PROMPT


def test_system_prompt_requires_reading_the_call_against_expectations():
    assert "CONTRE LES ATTENTES" in _SYSTEM_PROMPT
    assert "consensus" in _SYSTEM_PROMPT


def test_system_prompt_forbids_inventing_a_consensus_it_was_not_given():
    assert "n'inventes surtout pas un chiffre attendu" in _SYSTEM_PROMPT


def test_system_prompt_forbids_external_knowledge_and_buy_sell_advice():
    """
    Deliberately one assertion pair in its own test: these two limits
    were reconfirmed at the same moment the prompt was allowed to take a
    directional view, and relaxing the directional rule must never carry
    these away with it.
    """
    assert "AUCUN fait exterieur" in _SYSTEM_PROMPT
    assert "recommandation d'achat" in _SYSTEM_PROMPT


def test_system_prompt_forbids_dashes_in_the_answer():
    """
    Em dashes used as punctuation are a tell of generated text, banned
    on both sides: from the report template and from the answer.
    """
    assert "tiret cadratin" in _SYSTEM_PROMPT


def test_system_prompt_asks_for_the_five_sections_the_report_renders():
    for heading in (
        "## Verdict",
        "## Face aux attentes",
        "## Les declarations cles",
        "## Les esquives",
        "## A surveiller",
    ):
        assert heading in _SYSTEM_PROMPT


def test_system_prompt_states_the_length_budget():
    """
    Page 1 holds one page and no more. Asking for the right length up
    front is how the truncation safety net stays a safety net.
    """
    assert str(TARGET_WORDS_LOW) in _SYSTEM_PROMPT
    assert str(TARGET_WORDS_HIGH) in _SYSTEM_PROMPT
    assert TARGET_WORDS_LOW < TARGET_WORDS_HIGH


def test_the_prompt_itself_contains_no_em_dash():
    """A prompt that bans em dashes while using them teaches the opposite."""
    assert "—" not in _SYSTEM_PROMPT
    assert "–" not in _SYSTEM_PROMPT


# -- Calling it ---------------------------------------------------------


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_a_successful_call_returns_the_reading_with_its_provenance(monkeypatch):
    from equity_analyzer.report import claude_client

    monkeypatch.setattr(
        claude_client.requests, "post",
        lambda *a, **k: _Response(payload={"content": [{"text": "## Verdict\nPlutot bullish."}]}),
    )
    analysis = analyse_call(
        "AAOI", "2026Q1", TRANSCRIPT,
        api_key="sk-test", model="claude-sonnet-5", expectation=_expectation(),
    )

    assert analysis.text.startswith("## Verdict")
    assert analysis.model == "claude-sonnet-5"
    assert analysis.transcript_words == len(TRANSCRIPT.split())
    assert analysis.had_expectations is True


def test_a_reading_written_without_a_consensus_says_so_on_the_object(monkeypatch):
    from equity_analyzer.report import claude_client

    monkeypatch.setattr(
        claude_client.requests, "post",
        lambda *a, **k: _Response(payload={"content": [{"text": "Neutre."}]}),
    )
    analysis = analyse_call("AAOI", "2026Q1", TRANSCRIPT, api_key="sk-test")
    assert analysis.had_expectations is False


def test_an_empty_transcript_never_reaches_the_api():
    """
    A model handed nothing still answers, and answers from memory. The
    cheapest place to stop that is before the request.
    """
    called = []
    with pytest.raises(ClaudeError):
        analyse_call("AAOI", "2026Q1", "   ", api_key="sk-test")
    assert called == []


def test_a_failed_call_raises_rather_than_returning_placeholder_text(monkeypatch):
    """
    Page 1 IS the reading. A page 1 that degrades to an apology set in
    the same type as a real analysis is worse than no report.
    """
    from equity_analyzer.report import claude_client

    monkeypatch.setattr(
        claude_client.requests, "post",
        lambda *a, **k: _Response(status_code=429, text="rate limited"),
    )
    with pytest.raises(ClaudeError) as exc:
        analyse_call("AAOI", "2026Q1", TRANSCRIPT, api_key="sk-test")
    assert "429" in str(exc.value)
