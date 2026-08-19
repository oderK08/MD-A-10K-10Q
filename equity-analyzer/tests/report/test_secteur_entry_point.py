"""
The sector entry point as it runs: which records it loads, and what it
refuses.

The report step already proved the analysis; this pins the caller's
promises: it takes the latest record per ticker from the archive, needs
at least two usable ones, and never lets a missing or damaged file take
down the run.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "secteur.py"


def _entry_point(monkeypatch, tmp_path, argv, env=None):
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["secteur.py", *argv])

    spec = importlib.util.spec_from_file_location("secteur_ep", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPORTS_DIR = tmp_path
    return module


def _record(tmp_path, ticker, quarter="2026Q2", reading="## Verdict\nok"):
    (tmp_path / f"{ticker}.json").write_text(json.dumps({
        "ticker": ticker, "societe": ticker, "trimestre": quarter,
        "verdict": "ok", "lecture": reading, "attentes": None, "qa": None,
    }), encoding="utf-8")


def test_fewer_than_two_tickers_is_refused(monkeypatch, tmp_path):
    module = _entry_point(monkeypatch, tmp_path, ["NVDA"],
                          env={"ANTHROPIC_API_KEY": "k"})
    assert module.main() == 1


def test_a_missing_key_is_refused(monkeypatch, tmp_path):
    module = _entry_point(monkeypatch, tmp_path, ["NVDA", "AMD"])
    assert module.main() == 1


def test_a_group_with_fewer_than_two_archived_records_stops(monkeypatch, tmp_path):
    module = _entry_point(monkeypatch, tmp_path, ["NVDA", "AMD"],
                          env={"ANTHROPIC_API_KEY": "k"})
    _record(tmp_path, "NVDA")   # AMD has no archived record
    assert module.main() == 2


def test_a_full_run_loads_records_and_writes_a_pdf(monkeypatch, tmp_path):
    module = _entry_point(monkeypatch, tmp_path, ["nvda", "amd"],
                          env={"ANTHROPIC_API_KEY": "k"})
    _record(tmp_path, "NVDA")
    _record(tmp_path, "AMD")

    # Stub the model so no network is touched.
    from equity_analyzer.report import claude_client

    answer = json.dumps({
        "fil_directeur": "commun", "divergences": [], "questions_marche": [],
        "guidance": [], "read_throughs": [], "ton": "ton",
    })

    class _R:
        status_code = 200
        text = ""

        def json(self):
            return {"content": [{"type": "text", "text": answer}]}

    monkeypatch.setattr(claude_client.requests, "post", lambda *a, **k: _R())

    assert module.main() == 0
    pdfs = list(tmp_path.glob("secteur_*.pdf"))
    assert pdfs and pdfs[0].read_bytes()[:5] == b"%PDF-"
    assert list(tmp_path.glob("secteur_*.json"))


def test_a_damaged_record_is_skipped_not_fatal(monkeypatch, tmp_path):
    """A truncated archive file must cost that company, not the run."""
    module = _entry_point(monkeypatch, tmp_path, ["NVDA", "AMD", "AVGO"],
                          env={"ANTHROPIC_API_KEY": "k"})
    _record(tmp_path, "NVDA")
    _record(tmp_path, "AMD")
    (tmp_path / "AVGO.json").write_text('{"ticker": "AVGO", "lect')  # truncated

    from equity_analyzer.report import claude_client
    answer = json.dumps({"fil_directeur": "x", "divergences": [],
                         "questions_marche": [], "guidance": [],
                         "read_throughs": [], "ton": "x"})

    class _R:
        status_code = 200
        text = ""

        def json(self):
            return {"content": [{"type": "text", "text": answer}]}

    monkeypatch.setattr(claude_client.requests, "post", lambda *a, **k: _R())
    # NVDA + AMD remain usable, so the run still succeeds.
    assert module.main() == 0
