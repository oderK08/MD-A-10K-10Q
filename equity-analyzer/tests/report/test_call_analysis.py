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
    analyse_call,
    build_prompt,
    expectations_block,
    system_prompt,
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
#
# The prompt now has two shapes, one for a document with a Q&A page and
# one for a document without. Everything below that is a STANDING RULE
# is asserted on both: only the dodges section is allowed to differ, and
# a future edit that drops "no external facts" from one variant while
# leaving it in the other has to fail here.

BOTH_PROMPTS = pytest.mark.parametrize(
    "prompt",
    [system_prompt(qa_page=False), system_prompt(qa_page=True)],
    ids=["sans page Q&A", "avec page Q&A"],
)


@BOTH_PROMPTS
def test_system_prompt_requires_verbatim_quotes(prompt):
    assert "mot pour mot" in prompt
    assert "guillemets" in prompt


@BOTH_PROMPTS
def test_system_prompt_requires_separating_what_was_said_from_what_is_inferred(prompt):
    assert "DIT" in prompt and "DEDUIS" in prompt


@BOTH_PROMPTS
def test_system_prompt_requires_reading_the_call_against_expectations(prompt):
    assert "CONTRE LES ATTENTES" in prompt
    assert "consensus" in prompt


@BOTH_PROMPTS
def test_system_prompt_forbids_inventing_a_consensus_it_was_not_given(prompt):
    assert "n'inventes surtout pas un chiffre attendu" in prompt


@BOTH_PROMPTS
def test_system_prompt_forbids_external_knowledge_and_buy_sell_advice(prompt):
    """
    Deliberately one assertion pair in its own test: these two limits
    were reconfirmed at the same moment the prompt was allowed to take a
    directional view, and relaxing the directional rule must never carry
    these away with it.
    """
    assert "AUCUN fait exterieur" in prompt
    assert "recommandation d'achat" in prompt


@BOTH_PROMPTS
def test_system_prompt_forbids_dashes_in_the_answer(prompt):
    """
    Em dashes used as punctuation are a tell of generated text, banned
    on both sides: from the report template and from the answer.
    """
    assert "tiret cadratin" in prompt


@BOTH_PROMPTS
def test_system_prompt_always_asks_for_the_sections_page_one_renders(prompt):
    for heading in (
        "## Verdict",
        "## Face aux attentes",
        "## Les declarations cles",
        "## A surveiller",
    ):
        assert heading in prompt


def test_without_a_qa_page_the_reading_still_carries_the_dodges():
    """
    The dodges are the most informative part of a call. Moving them to
    page 2 is only acceptable because page 2 exists; when it does not,
    dropping them here would delete them from the document entirely.
    """
    prompt = system_prompt(qa_page=False)
    assert "## Les esquives" in prompt
    assert "cinq sections" in prompt


def test_with_a_qa_page_the_reading_is_told_not_to_repeat_the_dodges():
    """
    Measured on a real TSLA run: the same dodged question was printed as
    a paragraph on page 1 and as a row on page 2. Page 1 is hard capped,
    so that duplication is paid for by the datable commitments, which
    appear nowhere else.
    """
    prompt = system_prompt(qa_page=True)
    assert "## Les esquives" not in prompt
    assert "AUCUNE section" in prompt
    assert "quatre sections" in prompt


@BOTH_PROMPTS
def test_a_revised_figure_is_asked_for_as_a_change_not_as_a_level(prompt):
    """
    Found on a real MSFT report. The reading carried the capex, twice,
    but wrote "désormais ajusté à approximately $175 billion" without
    ever saying ajusté depuis quoi. A reader gets a level where the
    information is the revision.

    The model cannot always close that gap, because the only expectation
    it is handed is the EPS consensus: nothing tells it last quarter's
    capex guidance. What it CAN do is report the previous figure when
    management states it aloud, which on this kind of call is common,
    and say the comparison basis is missing when it does not.
    """
    assert "CE QUI A CHANGE PASSE EN PREMIER" in prompt
    assert "l'ancien, le nouveau et l'ampleur" in prompt
    assert "la base de comparaison manque" in prompt


@BOTH_PROMPTS
def test_a_revision_of_a_first_order_figure_belongs_in_the_verdict(prompt):
    """
    Same report: the verdict opened on Azure and the quality of the EPS
    beat, and never mentioned that the investment programme had moved.
    Nothing in the prompt said a change of that scale outranks a quarter
    that came in on line.
    """
    verdict = prompt.split("## Verdict")[1].split("##")[0]
    assert "capex" in verdict
    assert "changement d'echelle d'investissement" in verdict


@BOTH_PROMPTS
def test_system_prompt_states_the_length_budget(prompt):
    """
    Page 1 holds one page and no more. Asking for the right length up
    front is how the truncation safety net stays a safety net.
    """
    assert str(TARGET_WORDS_LOW) in prompt
    assert str(TARGET_WORDS_HIGH) in prompt
    assert TARGET_WORDS_LOW < TARGET_WORDS_HIGH


@BOTH_PROMPTS
def test_the_prompt_itself_contains_no_em_dash(prompt):
    """A prompt that bans em dashes while using them teaches the opposite."""
    assert "—" not in prompt
    assert "–" not in prompt


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
        lambda *a, **k: _Response(payload={"content": [{"type": "text", "text": "## Verdict\nPlutot bullish."}]}),
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
        lambda *a, **k: _Response(payload={"content": [{"type": "text", "text": "Neutre."}]}),
    )
    analysis = analyse_call("AAOI", "2026Q1", TRANSCRIPT, api_key="sk-test")
    assert analysis.had_expectations is False


def test_the_qa_page_flag_reaches_the_system_prompt_actually_sent(monkeypatch):
    """
    The variant is chosen inside `analyse_call`, so asserting on
    `system_prompt()` alone would not catch a caller wired to the wrong
    one. This reads the body of the request that went out.
    """
    from equity_analyzer.report import claude_client

    sent = {}

    def _capture(*a, **k):
        sent["system"] = k["json"]["system"]
        return _Response(payload={"content": [{"type": "text", "text": "Neutre."}]})

    monkeypatch.setattr(claude_client.requests, "post", _capture)

    analyse_call("AAOI", "2026Q1", TRANSCRIPT, api_key="sk-test", qa_page=True)
    assert "## Les esquives" not in sent["system"]

    analyse_call("AAOI", "2026Q1", TRANSCRIPT, api_key="sk-test", qa_page=False)
    assert "## Les esquives" in sent["system"]


def test_the_prior_guidance_baseline_reaches_the_request_before_the_transcript(monkeypatch):
    """
    Both baselines sit in FRONT of the call, so the model reads it
    already knowing what it is measured against rather than forming a
    view and then checking it. They answer different questions and
    neither replaces the other: the consensus says what the QUARTER was
    expected to earn, the guidance says what the COMPANY said it would
    do. A quarter can beat the first while quietly halving the second.
    """
    from equity_analyzer.report import claude_client

    sent = {}

    def _capture(*a, **k):
        sent["user"] = k["json"]["messages"][0]["content"]
        return _Response(payload={"content": [{"type": "text", "text": "Neutre."}]})

    monkeypatch.setattr(claude_client.requests, "post", _capture)
    analyse_call(
        "AAOI", "2026Q1", TRANSCRIPT, api_key="sk-test",
        expectation=_expectation(),
        prior_guidance="ENGAGEMENTS CHIFFRES PRIS AU TRIMESTRE PRECEDENT (2025Q4) :\n  capex : 80 milliards",
    )

    body = sent["user"]
    assert "capex : 80 milliards" in body
    assert body.index("ENGAGEMENTS CHIFFRES") < body.index("---DEBUT DU TRANSCRIPT---")
    assert body.index("ATTENTES DU MARCHE") < body.index("ENGAGEMENTS CHIFFRES")


def test_the_press_release_reaches_the_reading_too(monkeypatch):
    """
    Not only the Q&A pass. "Already public" against "said out loud only"
    matters just as much for the quantified commitments on page 1: a
    figure already in the release has been read by everyone, the same
    figure volunteered under questioning has not.
    """
    from equity_analyzer.report import claude_client

    sent = {}

    def _capture(*a, **k):
        sent["user"] = k["json"]["messages"][0]["content"]
        return _Response(payload={"content": [{"type": "text", "text": "Neutre."}]})

    monkeypatch.setattr(claude_client.requests, "post", _capture)
    analyse_call(
        "AAOI", "2026Q1", TRANSCRIPT, api_key="sk-test",
        press_release="COMMUNIQUE DE RESULTATS du trimestre 2026Q1\nle texte publie",
    )

    body = sent["user"]
    assert "le texte publie" in body
    assert body.index("COMMUNIQUE DE RESULTATS") < body.index("---DEBUT DU TRANSCRIPT---")


@BOTH_PROMPTS
def test_the_prompt_makes_unpublished_information_count_double(prompt):
    """
    Supplying the release is worth nothing if nothing tells the reading
    what to do with it. And the guard matters as much as the rule: with
    no release supplied, asserting that something was not in it is
    exactly the unfounded claim this whole change removes.
    """
    assert "CE QUI N'ETAIT PAS DEJA PUBLIC COMPTE DOUBLE" in prompt
    assert "tu n'affirmes pas qu'une information n'y figurait pas" in prompt


def test_without_a_baseline_the_prompt_carries_no_empty_guidance_block(monkeypatch):
    """
    An empty string means the caller is not using this at all, which is
    different from a caller that looked and found nothing. The second
    case sends the "no baseline" sentence, and that sentence is built by
    guidance_sheet, not improvised here.
    """
    from equity_analyzer.report import claude_client

    sent = {}

    def _capture(*a, **k):
        sent["user"] = k["json"]["messages"][0]["content"]
        return _Response(payload={"content": [{"type": "text", "text": "Neutre."}]})

    monkeypatch.setattr(claude_client.requests, "post", _capture)
    analyse_call("AAOI", "2026Q1", TRANSCRIPT, api_key="sk-test")

    assert "ENGAGEMENTS" not in sent["user"]


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


def test_an_implausible_gap_tells_the_model_not_to_build_a_verdict_on_it():
    """
    The page can print a caveat, but only the prompt can stop the model
    opening on "Mitige, le BPA manque le consensus de 82%" when that 82%
    is an artefact of comparing GAAP against adjusted.
    """
    from equity_analyzer.data_layer.earnings_expectations import QuarterExpectation

    block = expectations_block(QuarterExpectation(
        fiscal_date_ending=date(2026, 3, 31), reported_date=None,
        estimated_eps=0.71, reported_eps=0.13, surprise=-0.58, surprise_pct=-81.7,
    ))

    assert "ATTENTION" in block
    assert "AJUSTEE" in block
    assert "NE CONSTRUIS PAS" in block


def test_an_ordinary_gap_carries_no_such_warning():
    block = expectations_block(_expectation(estimated=1.00, reported=1.08, pct=8.0))
    assert "NE CONSTRUIS PAS" not in block


def test_a_machine_transcription_warns_the_model_about_numbers():
    """
    The page can warn the reader, but only the prompt can stop the model
    resting a verdict on a figure it may have misheard.
    """
    prompt = build_prompt("TEST", "2026Q2", TRANSCRIPT, verbatim=False)

    assert "AUTOMATIQUE" in prompt
    assert "CHIFFRES" in prompt
    assert prompt.index("AVERTISSEMENT SUR LA SOURCE") < prompt.index("DEBUT DU TRANSCRIPT")


def test_a_provider_transcript_gets_no_such_warning():
    assert "AVERTISSEMENT SUR LA SOURCE" not in build_prompt("TEST", "2026Q2", TRANSCRIPT)
