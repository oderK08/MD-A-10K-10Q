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


def test_the_previous_quarter_is_the_one_asked_for(monkeypatch):
    """
    The baseline is the quarter BEFORE the call being read, on the
    issuer's own fiscal calendar. Asking for the wrong one would compare
    this quarter's guidance against its own.
    """
    script = _entry_point()
    asked = {}

    class _Call:
        full_text = "un transcript"
        verbatim = True

    class _Source:
        def fetch(self, ticker, cik, client=None, quarter=None):
            asked["quarter"] = quarter
            return _Call()

    monkeypatch.setattr(script, "CachedTranscriptSource", lambda *a, **k: _Source())
    monkeypatch.setattr(script, "extract_guidance",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))

    script._prior_guidance_block("MSFT", "0000789019", "2027Q1", None, "Microsoft")
    # Rolls the year back at Q1, on the fiscal label and not the calendar.
    assert asked["quarter"] == "2026Q4"
