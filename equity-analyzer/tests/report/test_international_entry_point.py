"""
The international run as the entry point drives it.

THE GUARANTEE THAT MATTERS: this path never touches EDGAR. There is no
CIK to resolve and no filing to read the quarter off, so constructing an
EdgarClient at all would be a bug. The strongest way to prove that is to
make the client explode if anyone builds one, then run a full report and
watch it succeed anyway.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from equity_analyzer.report import claude_client

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "rapport.py"

# A deposited transcript long enough to pass the floor, with a clear
# handover so the Q&A is isolated.
_TRANSCRIPT = (
    "Good morning. " + ("Revenue grew across the cloud segment on strong "
    "backlog conversion and disciplined pricing. " * 120)
    + "\nOur first question comes from Jane Doe at Some Bank.\n"
    + ("Analyst: what is the margin outlook. Management: we prefer not to "
       "give a precise figure at this time. " * 80)
)

# The reading takes any text; the Q&A and the guidance extraction need a
# JSON object. Returning a valid Q&A object for every call satisfies all
# three: the reading just stores it as prose.
_JSON = json.dumps({
    "dodged_questions": [{"analyst": "Jane Doe", "question": "marge",
                          "what_was_asked": "un chiffre", "what_was_given": "rien",
                          "severity": "high"}],
    "concessions": [], "implicit_guidance": [], "recurring_themes": [],
    "uncertain_figures": [],
})


class _Response:
    status_code = 200
    text = ""

    def json(self):
        return {"content": [{"type": "text", "text": _JSON}]}


def _load(env, tmp_path, monkeypatch):
    """Load the script fresh with `env` set, dirs pointed at tmp."""
    for key in ("TICKER", "QUARTER", "REGION", "ANTHROPIC_API_KEY",
                "ANTHROPIC_MODEL", "ALPHAVANTAGE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    spec = importlib.util.spec_from_file_location("rapport_intl", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.CACHE_DIR = tmp_path / "transcripts"
    module.REPORTS_DIR = tmp_path / "rapports"
    module.HISTORY_DIR = tmp_path / "historique"
    module.CACHE_DIR.mkdir()

    # Any attempt to reach EDGAR is a bug on this path.
    def _no_edgar(*a, **k):
        raise AssertionError("le chemin international ne doit pas construire de client EDGAR")

    monkeypatch.setattr(module, "EdgarClient", _no_edgar)
    monkeypatch.setattr(claude_client.requests, "post", lambda *a, **k: _Response())
    return module


def _deposit(module, ticker, quarter, text=_TRANSCRIPT):
    (module.CACHE_DIR / f"{ticker}_{quarter}.txt").write_text(text)


def test_missing_quarter_is_refused_because_it_cannot_be_derived(tmp_path, monkeypatch):
    module = _load({"TICKER": "SAP", "REGION": "International",
                    "ANTHROPIC_API_KEY": "k"}, tmp_path, monkeypatch)
    assert module.main() == 1


def test_a_malformed_quarter_is_refused(tmp_path, monkeypatch):
    module = _load({"TICKER": "SAP", "QUARTER": "Q2-2026",
                    "REGION": "International", "ANTHROPIC_API_KEY": "k"},
                   tmp_path, monkeypatch)
    assert module.main() == 1


def test_a_missing_deposit_explains_how_to_add_one(tmp_path, monkeypatch, capsys):
    module = _load({"TICKER": "SAP", "QUARTER": "2026Q2",
                    "REGION": "International", "ANTHROPIC_API_KEY": "k"},
                   tmp_path, monkeypatch)
    assert module.main() == 2
    out = capsys.readouterr().out
    assert "SAP_2026Q2.txt" in out


def test_a_full_international_run_writes_a_pdf_without_touching_edgar(tmp_path, monkeypatch):
    module = _load({"TICKER": "SAP", "QUARTER": "2026Q2",
                    "REGION": "International", "ANTHROPIC_API_KEY": "k"},
                   tmp_path, monkeypatch)
    _deposit(module, "SAP", "2026Q2")

    assert module.main() == 0
    assert (module.REPORTS_DIR / "SAP.pdf").read_bytes()[:5] == b"%PDF-"


def test_the_run_stores_what_it_learned_for_next_quarter(tmp_path, monkeypatch):
    """
    History works the same offshore. The dodges of the quarter read are
    kept, so a later quarter finds them.
    """
    module = _load({"TICKER": "SAP", "QUARTER": "2026Q2",
                    "REGION": "International", "ANTHROPIC_API_KEY": "k"},
                   tmp_path, monkeypatch)
    _deposit(module, "SAP", "2026Q2")
    module.main()

    from equity_analyzer.data_layer.history import HistoryStore
    record = HistoryStore(module.HISTORY_DIR).get("SAP", "2026Q2")
    assert record is not None and record.dodges


def test_a_deposited_previous_quarter_becomes_the_baseline(tmp_path, monkeypatch):
    """
    The guidance baseline works offshore too, but only from a deposited
    file: no provider is ever consulted. With the previous quarter on
    disk, the reading gets its second reference point.
    """
    module = _load({"TICKER": "SAP", "QUARTER": "2026Q2",
                    "REGION": "International", "ANTHROPIC_API_KEY": "k"},
                   tmp_path, monkeypatch)
    _deposit(module, "SAP", "2026Q2")
    _deposit(module, "SAP", "2026Q1")

    # Alpha Vantage must never be reached; the local-only source has no
    # provider behind it, so a full run simply uses the deposited file.
    assert module.main() == 0


def test_the_us_path_is_untouched_when_region_is_default(tmp_path, monkeypatch):
    """
    Region absent means the US path, which DOES build an EdgarClient. The
    guard above would fire, proving the dispatch really branched.
    """
    module = _load({"TICKER": "SAP", "QUARTER": "2026Q2",
                    "ANTHROPIC_API_KEY": "k"}, tmp_path, monkeypatch)
    with pytest.raises(AssertionError, match="EDGAR"):
        module.main()
