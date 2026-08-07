"""
Tests for reading an earnings call.

Deliberately not a diff. The first live comparison of two Microsoft
calls came back at 7% sentence overlap -- management writes a fresh
script every quarter, so a diff reports that almost everything changed,
which is true and worthless. Reading the call is the right tool.
"""

import pytest

from equity_analyzer.report import ai_summary
from equity_analyzer.report.ai_summary import AISummaryError
from equity_analyzer.report.call_analysis import analyse_call, build_prompt

TRANSCRIPT = (
    "Operator\nWelcome to the fiscal 2026 third quarter earnings call.\n"
    "Satya Nadella -- Chief Executive Officer\n"
    "Microsoft Cloud revenue grew 22% this quarter.\n"
    "Amy Hood -- Chief Financial Officer\n"
    "We expect operating margins to remain roughly flat next quarter.\n"
)


class _Response:
    status_code = 200

    def __init__(self, text):
        self._text = text
        self.text = text

    def json(self):
        return {"content": [{"text": self._text}]}


def test_the_whole_transcript_goes_in_unabridged():
    """
    Trimming the call before the model reads it would mean deciding what
    matters first -- which is exactly the judgement being delegated.
    """
    prompt = build_prompt("MSFT", "2026Q3", TRANSCRIPT, "Microsoft")
    assert "Microsoft Cloud revenue grew 22%" in prompt
    assert "operating margins to remain roughly flat" in prompt
    assert "2026Q3" in prompt


def test_the_call_analysis_prompt_is_used_not_the_filing_diff_one(monkeypatch):
    """
    The two jobs share HTTP transport, error taxonomy and temperature=0,
    but not instructions. Sending the filing-diff prompt with a
    transcript would ask the model to compare quarters it was never
    given.
    """
    captured = {}

    def _post(url, headers=None, json=None, timeout=None):
        captured.update(json or {})
        return _Response("## L'essentiel\n- Un point.")

    monkeypatch.setattr(ai_summary.requests, "post", _post)
    analyse_call("MSFT", "2026Q3", TRANSCRIPT, api_key="sk-test")

    system = captured["system"]
    assert "earnings call" in system.lower()
    # the filing-diff prompt's defining instruction must not be in here
    assert "filings SEC D'UN TRIMESTRE SUR L'AUTRE" not in system


def test_the_prompt_demands_quotes_and_separates_said_from_inferred(monkeypatch):
    """
    Both properties come from the prompt, so the prompt is what gets
    tested. A transcript is a real verbatim record, so quoting is
    legitimate and it is the only way a reader can check the model
    against the source.
    """
    captured = {}
    monkeypatch.setattr(
        ai_summary.requests, "post",
        lambda url, headers=None, json=None, timeout=None: (
            captured.update(json or {}) or _Response("ok")
        ),
    )
    analyse_call("MSFT", "2026Q3", TRANSCRIPT, api_key="sk-test")

    system = captured["system"].lower()
    assert "mot pour mot" in system
    assert "guillemets" in system
    assert "deduis" in system or "déduis" in system


def test_an_empty_transcript_raises_rather_than_producing_an_analysis(monkeypatch):
    """
    A confident-looking analysis of nothing is the worst possible
    output: it reads exactly like a real one.
    """
    monkeypatch.setattr(
        ai_summary.requests, "post",
        lambda *a, **k: pytest.fail("l'API ne doit pas être appelée sans transcript"),
    )
    with pytest.raises(AISummaryError, match="transcript vide"):
        analyse_call("MSFT", "2026Q3", "   ", api_key="sk-test")


def test_a_missing_key_is_reported_before_any_call(monkeypatch):
    monkeypatch.setattr(
        ai_summary.requests, "post",
        lambda *a, **k: pytest.fail("l'API ne doit pas être appelée sans clé"),
    )
    with pytest.raises(AISummaryError, match="ANTHROPIC_API_KEY"):
        analyse_call("MSFT", "2026Q3", TRANSCRIPT, api_key="")


def test_the_word_count_is_carried_for_provenance(monkeypatch):
    monkeypatch.setattr(
        ai_summary.requests, "post",
        lambda *a, **k: _Response("## L'essentiel\n- Un point."),
    )
    analysis = analyse_call("MSFT", "2026Q3", TRANSCRIPT, api_key="sk-test")
    assert analysis.transcript_words == len(TRANSCRIPT.split())
    assert analysis.quarter == "2026Q3"


def test_temperature_is_omitted_for_models_that_reject_it(monkeypatch):
    """
    The Claude 5 family answers HTTP 400 "`temperature` is deprecated
    for this model". Sending it unconditionally made those models
    unusable -- and the failure landed after the transcript had already
    been fetched and paid for.
    """
    captured = {}
    monkeypatch.setattr(
        ai_summary.requests, "post",
        lambda url, headers=None, json=None, timeout=None: (
            captured.update(json or {}) or _Response("ok")
        ),
    )

    analyse_call("MSFT", "2026Q3", TRANSCRIPT, api_key="sk-test", model="claude-sonnet-5")
    assert "temperature" not in captured

    captured.clear()
    analyse_call("MSFT", "2026Q3", TRANSCRIPT, api_key="sk-test",
                 model="claude-haiku-4-5-20251001")
    assert captured["temperature"] == 0


def test_the_family_is_matched_not_an_exact_model_list():
    """
    New members of the family ship without this file changing, and a 400
    on an unknown-but-current model is the worst outcome: by then the
    transcript is already fetched.
    """
    from equity_analyzer.report.ai_summary import _accepts_temperature

    assert not _accepts_temperature("claude-opus-5")
    assert not _accepts_temperature("claude-fable-5")
    assert not _accepts_temperature("claude-sonnet-7-some-future-suffix")
    # ...while 4.5 and earlier still take it
    assert _accepts_temperature("claude-haiku-4-5-20251001")
    assert _accepts_temperature("")
