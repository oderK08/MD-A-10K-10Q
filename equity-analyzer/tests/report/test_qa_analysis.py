"""
The Q&A pass: a second reading of the half of the transcript page 1 only
skims, returned as data rather than prose.

The parsing tests carry most of the weight. A model told to answer in
bare JSON mostly does, and the times it does not are exactly the times
the transcript has already been fetched and paid for.
"""

from __future__ import annotations

import json

import pytest

from equity_analyzer.report import claude_client
from equity_analyzer.report.claude_client import ClaudeError
from equity_analyzer.report.qa_analysis import (
    QaAnalysis,
    analyse_qa,
    build_prompt,
    parse_response,
    system_prompt,
)

_ANSWER = {
    "period": "Q2 2026",
    "dodged_questions": [
        {"analyst": "Jane Doe", "question": "part du plus gros client",
         "what_was_asked": "le pourcentage exact", "what_was_given": "diversification",
         "severity": "high"},
        {"analyst": "John Roe", "question": "calendrier AV",
         "what_was_asked": "une date", "what_was_given": "bientot", "severity": "low"},
    ],
    "concessions": [{"topic": "marge", "admission": "pression admise",
                     "verbatim": "margins declined sequentially"}],
    "implicit_guidance": [{"topic": "capex", "signal": "hausse au T3",
                           "buried_in": "reponse sur la tresorerie", "direction": "negative"}],
    "recurring_themes": [{"theme": "concurrence", "analyst_count": 3, "summary": "trois fois"}],
    # Still sent by a model that has seen an older prompt, and dropped
    # on the way in rather than carried as a field nothing renders.
    "tone_shift_markers": ["we are being careful here"],
    "uncertain_figures": ["15% ou 50%, inaudible"],
}


class _Response:
    def __init__(self, text):
        self.status_code = 200
        self.text = ""
        self._text = text

    def json(self):
        return {"content": [{"type": "text", "text": self._text}]}


def _answers(monkeypatch, text):
    monkeypatch.setattr(claude_client.requests, "post", lambda *a, **k: _Response(text))


# -- What the prompt is required to say --------------------------------


def test_the_transcription_warning_is_conditional_on_the_source():
    """
    THE adaptation that matters. A provider transcript is the company's
    own written record; telling the model to doubt its numbers would
    make it hedge on figures that are exactly right.
    """
    machine = system_prompt(verbatim=False)
    official = system_prompt(verbatim=True)

    assert "transcription est automatique" in machine
    assert "transcription est automatique" not in official
    assert "compte rendu ecrit officiel" in official


def test_the_prompt_forbids_external_facts_like_the_rest_of_the_project():
    assert "AUCUN fait exterieur" in system_prompt()


def test_the_prompt_forbids_dashes_in_every_field():
    assert "tiret cadratin" in system_prompt()


def test_the_prompt_itself_uses_no_em_dash():
    for text in (system_prompt(True), system_prompt(False)):
        assert "—" not in text and "–" not in text


def test_only_the_qa_half_is_sent():
    """
    The prepared remarks are page 1's material. Sending them here would
    pay twice for the same tokens and blur what this pass is looking at.
    """
    prompt = build_prompt("UBER", "2026Q2", "LA SESSION SEULEMENT")
    assert "LA SESSION SEULEMENT" in prompt
    assert "questions-reponses" in prompt


# -- Reading the answer back -------------------------------------------


def test_a_clean_json_answer_is_parsed(monkeypatch):
    _answers(monkeypatch, json.dumps(_ANSWER))
    result = analyse_qa("UBER", "2026Q2", "des questions", api_key="k")

    assert len(result.dodged_questions) == 2
    assert len(result.hard_dodges) == 1
    assert result.implicit_guidance[0]["direction"] == "negative"
    assert result.uncertain_figures == ["15% ou 50%, inaudible"]
    assert result.is_empty is False


def test_a_key_the_report_no_longer_renders_is_ignored_rather_than_stored():
    """
    "Formulations notables" was dropped from the document, so the schema
    stopped asking for it. A model can still return it: from an older
    prompt, or simply because it felt like it. That key must not become
    an attribute nobody reads, which is how a removed section grows back
    by accident.
    """
    assert not hasattr(QaAnalysis(ticker="X", quarter="2026Q1", model="m"),
                       "tone_shift_markers")


def test_a_fenced_answer_is_recovered(monkeypatch):
    """
    A model told to answer in bare JSON still wraps it in a fence often
    enough that not handling it would throw away a good answer, with the
    transcript already fetched and paid for.
    """
    _answers(monkeypatch, "```json\n" + json.dumps(_ANSWER) + "\n```")
    assert len(analyse_qa("UBER", "2026Q2", "q", api_key="k").concessions) == 1


def test_a_sentence_around_the_object_is_tolerated():
    payload = parse_response('Voici le resultat :\n{"period": "Q2"}\nJ\'espere que ca aide.')
    assert payload["period"] == "Q2"


def test_an_answer_with_no_json_object_raises():
    """
    Strict about the content on purpose: a partially understood answer
    rendered as findings is worse than no section at all.
    """
    with pytest.raises(ClaudeError):
        parse_response("Je ne peux pas repondre.")


def test_broken_json_raises_rather_than_returning_half_a_reading():
    with pytest.raises(ClaudeError):
        parse_response('{"dodged_questions": [')


def test_a_malformed_entry_costs_that_entry_and_not_the_section(monkeypatch):
    """
    One bad row in a list of six should cost that row. Kept, it would
    render as a blank line, and a blank line reads as a finding with
    nothing in it.
    """
    answer = dict(_ANSWER, dodged_questions=[
        _ANSWER["dodged_questions"][0], "pas un objet", {}, {"analyst": "Real"},
    ])
    _answers(monkeypatch, json.dumps(answer))
    result = analyse_qa("UBER", "2026Q2", "q", api_key="k")

    assert len(result.dodged_questions) == 2


def test_missing_keys_come_back_as_empty_lists(monkeypatch):
    _answers(monkeypatch, '{"period": "Q2 2026"}')
    result = analyse_qa("UBER", "2026Q2", "q", api_key="k")

    assert result.dodged_questions == []
    assert result.is_empty is True
    assert result.declared_period == "Q2 2026"


def test_an_empty_session_never_reaches_the_api():
    with pytest.raises(ClaudeError):
        analyse_qa("UBER", "2026Q2", "   ", api_key="k")


def test_the_declared_period_is_kept_but_never_becomes_the_quarter(monkeypatch):
    """
    EDGAR already answered which quarter this is. The model's own view is
    worth having as a third independent check on the pairing, and worth
    nothing as a source of truth.
    """
    _answers(monkeypatch, json.dumps(dict(_ANSWER, period="Q4 2025")))
    result = analyse_qa("UBER", "2026Q2", "q", api_key="k")

    assert result.quarter == "2026Q2"
    assert result.declared_period == "Q4 2025"
