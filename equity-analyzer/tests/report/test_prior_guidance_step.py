"""
The baseline step as the entry point runs it.

WHY A TEST OF THE SCRIPT AND NOT JUST THE MODULE. `guidance_sheet` is
tested on its own, but the guarantee that matters is a property of the
CALLER: this step is optional, it happens after a transcript has been
fetched and paid for, and no failure inside it may cost the report.
Testing the module alone would leave that promise unverified, which is
exactly how the page budget regression got through once already.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "rapport.py"


def _entry_point():
    """The script, loaded by path: `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("rapport_entry_point", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("boom", [
    RuntimeError("le fournisseur a renvoye du HTML"),
    ValueError("entree de cache illisible"),
    KeyError("champ absent"),
])
def test_no_failure_fetching_the_previous_call_can_cost_the_report(monkeypatch, boom):
    """
    Anything at all, not just the transcript exceptions.

    By the time this runs the current transcript is fetched and paid
    for, so aborting here would throw away the run AND the quota. The
    step degrades to a printed reason and a block that tells the model
    it has no baseline.
    """
    script = _entry_point()
    monkeypatch.setattr(script, "CachedTranscriptSource",
                        lambda *a, **k: (_ for _ in ()).throw(boom))

    block = script._prior_guidance_block("MSFT", "0000789019", "2026Q4", None, "Microsoft")

    assert "NON DISPONIBLES" in block
    assert "tu n'affirmes aucune hausse ni aucune baisse" in block


def test_a_failure_extracting_the_commitments_is_equally_survivable(monkeypatch):
    """The second half of the step, same promise."""
    script = _entry_point()

    class _Call:
        full_text = "un transcript"
        verbatim = True

    class _Source:
        def fetch(self, *a, **k):
            return _Call()

    monkeypatch.setattr(script, "CachedTranscriptSource", lambda *a, **k: _Source())
    monkeypatch.setattr(script, "extract_guidance",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")))

    block = script._prior_guidance_block("MSFT", "0000789019", "2026Q4", None, "Microsoft")

    assert "NON DISPONIBLES" in block
    assert "extraction de 2026Q3" in block


class _Call:
    full_text = "un transcript"
    verbatim = True


def _source_that(available):
    """A source holding transcripts for exactly `available` quarters."""
    from equity_analyzer.data_layer.transcript_source import TranscriptUnavailable

    class _Source:
        def __init__(self):
            self.asked = []

        def fetch(self, ticker, cik, client=None, quarter=None):
            self.asked.append(quarter)
            if quarter in available:
                return _Call()
            raise TranscriptUnavailable(f"rien pour {quarter}")

    return _Source()


def test_the_previous_quarter_is_the_one_asked_for_first(monkeypatch):
    """
    The baseline is the quarter BEFORE the call being read, on the
    issuer's own fiscal calendar. Asking for the wrong one would compare
    this quarter's guidance against its own.
    """
    script = _entry_point()
    source = _source_that({"2026Q4"})
    monkeypatch.setattr(script, "CachedTranscriptSource", lambda *a, **k: source)
    monkeypatch.setattr(script, "extract_guidance",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))

    script._prior_guidance_block("MSFT", "0000789019", "2027Q1", None, "Microsoft")
    # Rolls the year back at Q1, on the fiscal label and not the calendar.
    assert source.asked[0] == "2026Q4"


def test_a_missing_quarter_is_walked_past_rather_than_losing_the_baseline(monkeypatch):
    """
    One quarter the provider never published used to cost the baseline
    entirely. It now steps back, and reports how far it went so the
    prompt can qualify the comparison.
    """
    script = _entry_point()
    source = _source_that({"2026Q2"})
    captured = {}

    def _extract(ticker, quarter, text, **kwargs):
        captured.update(quarter=quarter, quarters_before=kwargs["quarters_before"])
        raise RuntimeError("stop")

    monkeypatch.setattr(script, "CachedTranscriptSource", lambda *a, **k: source)
    monkeypatch.setattr(script, "extract_guidance", _extract)

    script._prior_guidance_block("MSFT", "0000789019", "2026Q4", None, "Microsoft")

    assert source.asked == ["2026Q3", "2026Q2"]
    assert captured["quarter"] == "2026Q2"
    assert captured["quarters_before"] == 2


def test_the_walk_is_bounded_rather_than_spending_the_daily_budget(monkeypatch):
    """
    Every step is a provider request out of a budget of 25 that the
    report's own transcript and the consensus also draw on. And a
    baseline decays: against a year ago, "did this change today" has
    almost no relationship to the answer.
    """
    script = _entry_point()
    source = _source_that(set())
    monkeypatch.setattr(script, "CachedTranscriptSource", lambda *a, **k: source)

    block = script._prior_guidance_block("MSFT", "0000789019", "2026Q4", None, "Microsoft")

    assert len(source.asked) == script.MAX_BASELINE_QUARTERS_BACK
    assert "NON DISPONIBLES" in block


def test_a_quota_refusal_stops_the_walk_at_once(monkeypatch):
    """
    Quota exhaustion applies to every subsequent request too, so
    stepping back again would spend the rest of the day collecting the
    same error. Same rule as the main transcript search.
    """
    from equity_analyzer.data_layer.transcript_source import TranscriptRefused

    script = _entry_point()
    asked = []

    class _Source:
        def fetch(self, ticker, cik, client=None, quarter=None):
            asked.append(quarter)
            raise TranscriptRefused("quota épuisé")

    monkeypatch.setattr(script, "CachedTranscriptSource", lambda *a, **k: _Source())

    block = script._prior_guidance_block("MSFT", "0000789019", "2026Q4", None, "Microsoft")

    assert asked == ["2026Q3"]
    assert "NON DISPONIBLES" in block
