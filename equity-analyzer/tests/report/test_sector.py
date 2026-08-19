"""
The cross-company synthesis: the prompt's discipline, the parsing, and
the layout.

The value of a sector view is the comparison, so the rules that keep it
honest are the same as the single report's, applied to a group: no
valuation, no recommendation, everything attributed to a named company.
Most of what follows pins exactly those.
"""

from __future__ import annotations

import json

import pytest

from equity_analyzer.report import claude_client
from equity_analyzer.report.claude_client import ClaudeError
from equity_analyzer.report.sector import (
    SectorSynthesis,
    analyse_sector,
    build_prompt,
    parse_response,
    system_prompt,
)
from equity_analyzer.report.sector_render import render_sector_html
from equity_analyzer.report.pdf_renderer import page_count, render_pdf

_RECORDS = [
    {"ticker": "NVDA", "societe": "NVIDIA", "trimestre": "2026Q2",
     "verdict": "Plutot bullish, la demande deborde l'offre.",
     "lecture": "## Verdict\nBullish. \"demand exceeds supply\"",
     "attentes": {"disponible": True, "bpa_attendu": 1.0, "bpa_publie": 1.1,
                  "surprise_pct": 10.0},
     "qa": {"esquives": [{"analyst": "X", "what_was_asked": "marge",
                          "what_was_given": "rien", "severity": "high"}],
            "signaux_prospectifs": [{"signal": "capex up", "direction": "negative"}]}},
    {"ticker": "AMD", "societe": "AMD", "trimestre": "2026Q2",
     "verdict": "Mitige, pression sur les prix.",
     "lecture": "## Verdict\nMitige. \"pricing pressure in some segments\"",
     "attentes": {"disponible": False, "raison": "quota"},
     "qa": {}},
]

_ANSWER = {
    "fil_directeur": "La demande IA reste le moteur commun. " * 6,
    "divergences": [{"driver": "pricing",
                     "positions": [{"ticker": "NVDA", "position": "tient"},
                                   {"ticker": "AMD", "position": "concede"}],
                     "qui_diverge": "AMD seul admet la pression"}],
    "questions_marche": [{"sujet": "marge", "presse_chez": ["NVDA", "AMD"],
                          "repondu_par": ["NVDA"], "esquive_par": ["AMD"],
                          "lecture": "inquietude sectorielle"}],
    "guidance": [{"metrique": "capex",
                  "par_societe": [{"ticker": "NVDA", "mouvement": "releve", "detail": "20 mds"}]}],
    "read_throughs": [{"de": "AVGO", "vers": "NVDA", "implication": "custom silicon",
                       "base": "AVGO cite trois hyperscalers"}],
    "ton": "NVDA confiant, AMD hedge. " * 4,
}


class _Response:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _answers(monkeypatch, text):
    monkeypatch.setattr(
        claude_client.requests, "post",
        lambda *a, **k: _Response({"content": [{"type": "text", "text": text}]}),
    )


# -- The prompt's discipline -------------------------------------------


def test_the_prompt_forbids_valuation_and_recommendation():
    prompt = system_prompt()
    assert "aucune valorisation" in prompt.lower()
    assert "aucune recommandation" in prompt.lower()


def test_the_prompt_requires_attribution_to_a_named_company():
    assert "ATTRIBUEE a une societe nommee" in system_prompt()


def test_the_prompt_puts_the_value_on_divergence_and_consensus():
    prompt = system_prompt()
    assert "divergence" in prompt.lower()
    assert "deja dans les cours" in prompt.lower()


def test_the_prompt_contains_no_em_dash():
    assert "—" not in system_prompt() and "–" not in system_prompt()


def test_each_company_and_its_quarter_reach_the_prompt():
    prompt = build_prompt(_RECORDS)
    assert "NVDA" in prompt and "AMD" in prompt
    assert "2026Q2" in prompt
    assert "demand exceeds supply" in prompt


# -- Reading the answer ------------------------------------------------


def test_a_clean_answer_is_parsed(monkeypatch):
    _answers(monkeypatch, json.dumps(_ANSWER))
    synthesis = analyse_sector(_RECORDS, api_key="k")

    assert synthesis.tickers == ["NVDA", "AMD"]
    assert synthesis.quarters == {"NVDA": "2026Q2", "AMD": "2026Q2"}
    assert len(synthesis.divergences) == 1
    assert synthesis.read_throughs[0]["vers"] == "NVDA"


def test_fewer_than_two_companies_is_refused():
    with pytest.raises(ClaudeError):
        analyse_sector(_RECORDS[:1], api_key="k")


def test_a_non_json_answer_raises(monkeypatch):
    _answers(monkeypatch, "voici la synthese en prose")
    with pytest.raises(ClaudeError):
        analyse_sector(_RECORDS, api_key="k")


def test_mixed_quarters_is_detected():
    synthesis = SectorSynthesis(
        tickers=["A", "B"], quarters={"A": "2026Q2", "B": "2026Q3"}, model="m")
    assert synthesis.mixed_quarters is True

    aligned = SectorSynthesis(
        tickers=["A", "B"], quarters={"A": "2026Q2", "B": "2026Q2"}, model="m")
    assert aligned.mixed_quarters is False


# -- Layout ------------------------------------------------------------


def _synthesis(**overrides):
    kwargs = dict(tickers=["NVDA", "AMD"],
                  quarters={"NVDA": "2026Q2", "AMD": "2026Q2"}, model="m",
                  **{k: _ANSWER[k] for k in
                     ("fil_directeur", "divergences", "questions_marche",
                      "guidance", "read_throughs", "ton")})
    kwargs.update(overrides)
    return SectorSynthesis(**kwargs)


def test_the_document_renders_every_section():
    html = render_sector_html(_synthesis())
    for heading in ("Le fil directeur", "Les divergences", "Ce que le marche redoute",
                    "Guidance", "Read-throughs", "Ton et posture"):
        assert heading in html


def test_a_mixed_quarter_group_is_warned_on_the_page():
    html = render_sector_html(_synthesis(quarters={"NVDA": "2026Q2", "AMD": "2026Q3"}))
    assert "Trimestres non alignes" in html


def test_an_aligned_group_carries_no_such_warning():
    html = render_sector_html(_synthesis())
    assert "Trimestres non alignes" not in html


def test_the_footer_states_the_limits():
    html = render_sector_html(_synthesis())
    assert "Aucune" in html and "valorisation" in html
    assert "Pas de recommandation" in html


def test_it_renders_to_a_real_pdf():
    pdf = render_pdf(render_sector_html(_synthesis()))
    assert pdf[:5] == b"%PDF-"
    assert page_count(pdf) >= 1


def test_empty_axes_are_dropped_rather_than_rendered_blank():
    html = render_sector_html(_synthesis(
        divergences=[], guidance=[], read_throughs=[]))
    assert "Les divergences" not in html
    assert "Read-throughs" not in html
    # Prose sections that ARE present still render.
    assert "Le fil directeur" in html


def test_no_em_dash_reaches_the_rendered_page():
    html = render_sector_html(_synthesis())
    assert "—" not in html and "–" not in html
