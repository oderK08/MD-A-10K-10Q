"""
The comparison baseline: last quarter's quantified commitments.

WHAT THESE TESTS ARE PROTECTING. The reading was measured against the
EPS consensus and nothing else, so a capex envelope arrived with no
reference point and could only be reported as a level. A real MSFT
report quoted the capex twice and wrote "desormais ajuste a
approximately $175 billion" without ever saying adjusted from what.

The failure mode this introduces is worse than the one it fixes, and
most of what follows guards against it: a model that thinks it has a
baseline when it does not will write "en hausse par rapport au
trimestre precedent" from memory, and on the page that sentence is
indistinguishable from one written from the text.
"""

from __future__ import annotations

import json

import pytest

from equity_analyzer.report import claude_client
from equity_analyzer.report.claude_client import ClaudeError
from equity_analyzer.report.guidance_sheet import (
    GuidanceSheet,
    as_prompt_block,
    build_prompt,
    extract_guidance,
    parse_response,
    system_prompt,
)

_ANSWER = {
    "commitments": [
        {"metric": "capex", "value": "environ 80 milliards de dollars",
         "period": "annee civile 2025", "verbatim": "capex of approximately $80 billion"},
        {"metric": "croissance Azure", "value": "environ 35% a change constant",
         "period": "T1", "verbatim": "we expect Azure growth of approximately 35%"},
    ]
}

TRANSCRIPT = "Nous attendons un capex d'environ 80 milliards. " * 40


class _Response:
    def __init__(self, text):
        self.status_code = 200
        self._text = text
        self.text = text

    def json(self):
        return {"content": [{"type": "text", "text": self._text}]}


def _answers(monkeypatch, text):
    monkeypatch.setattr(claude_client.requests, "post", lambda *a, **k: _Response(text))


# -- What the prompt is required to say --------------------------------


@pytest.mark.parametrize("verbatim", [True, False])
def test_the_prompt_refuses_commitments_without_a_figure(verbatim):
    """
    The whole point is comparability. "Nous restons confiants" cannot be
    compared to anything next quarter, and a baseline row with no number
    in it is a row the reading cannot use.
    """
    prompt = system_prompt(verbatim)
    assert "Tu ne retiens rien sans chiffre" in prompt
    assert "n'est pas un engagement" in prompt


@pytest.mark.parametrize("verbatim", [True, False])
def test_the_prompt_forbids_interpreting_and_importing(verbatim):
    """
    This pass is raw material. Judging what matters happens one layer
    up, in the reading, where the current quarter is also in view.
    """
    prompt = system_prompt(verbatim)
    assert "Tu n'interpretes pas" in prompt
    assert "AUCUN fait exterieur" in prompt


@pytest.mark.parametrize("verbatim", [True, False])
def test_the_prompt_forbids_converting_the_values(verbatim):
    """
    A value rewritten is a value that no longer matches next quarter's
    wording, and a model that computes is a model that can compute
    wrong. "More than $25 billion" stays exactly that.
    """
    prompt = " ".join(system_prompt(verbatim).split())
    assert "Tu ne convertis pas, tu n'arrondis pas, tu ne calcules rien" in prompt
    assert "La valeur est reprise telle quelle" in prompt


def test_a_machine_transcription_is_flagged_to_the_model():
    assert "transcription est automatique" in system_prompt(verbatim=False)
    assert "transcription est automatique" not in system_prompt(verbatim=True)


@pytest.mark.parametrize("verbatim", [True, False])
def test_the_prompt_itself_contains_no_em_dash(verbatim):
    prompt = system_prompt(verbatim)
    assert "—" not in prompt and "–" not in prompt


# -- Reading the answer ------------------------------------------------


def test_a_clean_answer_is_parsed(monkeypatch):
    _answers(monkeypatch, json.dumps(_ANSWER))
    sheet = extract_guidance("MSFT", "2026Q3", TRANSCRIPT, api_key="k")

    assert len(sheet.commitments) == 2
    assert sheet.commitments[0]["value"] == "environ 80 milliards de dollars"
    assert sheet.quarter == "2026Q3"
    assert sheet.is_empty is False


def test_a_fenced_answer_is_recovered(monkeypatch):
    _answers(monkeypatch, f"```json\n{json.dumps(_ANSWER)}\n```")
    assert len(extract_guidance("MSFT", "2026Q3", TRANSCRIPT, api_key="k").commitments) == 2


def test_an_entry_without_a_figure_is_dropped_rather_than_kept(monkeypatch):
    """
    Dropped, because a baseline row with no value is exactly what the
    prompt said not to return and the reading cannot compare it to
    anything. Dropped and not raised, because one bad entry should cost
    that entry and not the whole baseline.
    """
    _answers(monkeypatch, json.dumps({"commitments": [
        {"metric": "capex", "value": "environ 80 milliards", "period": "2025"},
        {"metric": "confiance", "value": "", "period": ""},
        {"metric": "", "value": "12%", "period": ""},
        "pas un objet",
    ]}))
    sheet = extract_guidance("MSFT", "2026Q3", TRANSCRIPT, api_key="k")

    assert len(sheet.commitments) == 1
    assert sheet.commitments[0]["metric"] == "capex"


def test_a_non_json_answer_raises(monkeypatch):
    _answers(monkeypatch, "Voici les engagements du trimestre.")
    with pytest.raises(ClaudeError):
        extract_guidance("MSFT", "2026Q3", TRANSCRIPT, api_key="k")


def test_an_empty_transcript_never_reaches_the_api():
    with pytest.raises(ClaudeError):
        extract_guidance("MSFT", "2026Q3", "   ", api_key="k")


def test_the_transcript_reaches_the_prompt_whole():
    prompt = build_prompt("MSFT", "2026Q3", TRANSCRIPT, "Microsoft Corp")
    assert TRANSCRIPT in prompt
    assert "Microsoft Corp" in prompt


# -- How the reading sees it -------------------------------------------


def test_the_block_lists_the_commitments_and_asks_for_the_gap():
    block = as_prompt_block(GuidanceSheet(
        ticker="MSFT", quarter="2026Q3", model="m",
        commitments=_ANSWER["commitments"],
    ))

    assert "2026Q3" in block
    assert "environ 80 milliards de dollars" in block
    assert "annee civile 2025" in block
    # The instruction that makes the baseline useful rather than decorative.
    assert "l'ancien, le nouveau et l'ampleur" in block


def test_the_block_tells_the_reading_not_to_analyse_the_old_quarter():
    """
    It is a ruler, not material. Without this the model spends words of
    a hard capped page commenting on a quarter the reader did not ask
    about.
    """
    block = as_prompt_block(GuidanceSheet(
        ticker="MSFT", quarter="2026Q3", model="m",
        commitments=_ANSWER["commitments"],
    ))
    assert "ne commente pas le trimestre precedent" in block


def test_a_missing_baseline_is_stated_loudly_rather_than_left_out():
    """
    THE failure mode this whole feature could introduce. A model handed
    no baseline does not conclude the baseline is unknown, it reaches
    for whatever it remembers about the company, and "en hausse par
    rapport au trimestre precedent" written from memory looks exactly
    like one written from the text.
    """
    block = as_prompt_block(None, reason="call 2026Q3 indisponible")

    assert "NON DISPONIBLES" in block
    assert "call 2026Q3 indisponible" in block
    assert "tu n'affirmes aucune hausse ni aucune baisse" in block


def test_an_empty_sheet_is_treated_as_no_baseline_at_all():
    """
    A call that committed to nothing quantified and a call we could not
    read are the same thing for the reading: there is nothing to compare
    against, and it must not think otherwise.
    """
    block = as_prompt_block(GuidanceSheet(ticker="M", quarter="2026Q3", model="m"))
    assert "NON DISPONIBLES" in block


def test_a_revision_stated_in_the_call_itself_stays_allowed():
    """
    Without a baseline the model still has one honest route: management
    naming the old figure out loud. Forbidding that too would throw away
    the case the prompt fix already handles.
    """
    assert "SI LA DIRECTION LA CHIFFRE ELLE MEME" in as_prompt_block(None)
