"""
The press release step as the entry point runs it.

Same promise as the guidance baseline step, and the same reason for
testing it here rather than only testing the module: this runs after a
transcript has been fetched and paid for, and no failure inside a
nicety may cost the report.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "rapport.py"


def _entry_point():
    spec = importlib.util.spec_from_file_location("rapport_entry_point", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("boom", [
    RuntimeError("EDGAR a renvoyé du HTML inattendu"),
    ValueError("index de dépôt illisible"),
    KeyError("exhibit absent"),
])
def test_no_failure_fetching_the_release_can_cost_the_report(monkeypatch, boom):
    script = _entry_point()
    monkeypatch.setattr(script, "fetch_press_release",
                        lambda *a, **k: (_ for _ in ()).throw(boom))

    block = script._press_release_block("0000789019", "2026Q4", None)

    assert "NON DISPONIBLE" in block
    assert "N'affirme jamais qu'une information ne figurait pas" in block


def test_a_paired_release_is_handed_over_as_the_yardstick(monkeypatch):
    from equity_analyzer.data_layer.press_release import PressRelease

    script = _entry_point()
    monkeypatch.setattr(script, "fetch_press_release", lambda *a, **k: PressRelease(
        quarter="2026Q4", text="ce que le marché savait déjà",
        document="ex991.htm", accession_number="0001",
    ))

    block = script._press_release_block("0000789019", "2026Q4", None)

    assert "ce que le marché savait déjà" in block
    assert "DEJA PUBLIC" in block


def test_the_release_is_asked_for_the_quarter_being_read(monkeypatch):
    """
    Not "the newest": the report can be reading a call one quarter back
    when the provider published late, and pairing the release to the
    wrong quarter is the failure this feature could introduce.
    """
    script = _entry_point()
    asked = {}

    def _fetch(client, cik, label, *a, **k):
        asked["label"] = label
        raise RuntimeError("stop")

    monkeypatch.setattr(script, "fetch_press_release", _fetch)
    script._press_release_block("0000789019", "2026Q2", None)

    assert asked["label"] == "2026Q2"
