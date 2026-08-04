"""
Runs the full equity_analyzer pipeline (Modules 1-5) against the REAL SEC
EDGAR API for one ticker, and saves a real PDF report.

Meant to be triggered from GitHub Actions (see
.github/workflows/test-real-sec-api.yml), which runs on GitHub's own
servers -- unlike the sandboxes this project was developed in, those have
normal internet access, so this is the first time the pipeline runs
against live data instead of the fixtures under tests/fixtures/.

Reads its ticker and SEC User-Agent from environment variables so the
workflow's input boxes (filled in the GitHub Actions UI, no terminal
needed) can control them without editing this file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from equity_analyzer.data_layer import (
    CikLookup,
    EdgarClient,
    EdgarClientConfig,
    EdgarClientError,
    FilingNotFoundError,
    Filing,
    FormType,
    build_financial_period,
    extract_sections,
    list_filings,
)
from equity_analyzer.report import build_report_data, render_html, save_pdf
from equity_analyzer.sentiment import load_lm_dictionary

TICKER = os.environ.get("TICKER", "AAPL").strip().upper()
USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityAnalyzer/1.0 contact@example.com").strip()
DICTIONARY_PATH = Path(__file__).parent.parent / "data" / "Loughran-McDonald_MasterDictionary_1993-2025.csv"
OUTPUT_PDF = Path(__file__).parent.parent / "rapport_genere.pdf"


def _ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"[SKIP] {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def _build_filing(client, cik, filing_ref, ticker, company_name) -> Filing:
    company_facts = client.fetch_company_facts(cik)
    financials = build_financial_period(
        company_facts,
        accession_number=filing_ref.accession_number,
        fiscal_year=(filing_ref.period_of_report or filing_ref.filed_date).year,
        fiscal_period="FY",
    )
    html = client.fetch_filing_document(cik, filing_ref.accession_number, filing_ref.primary_document)
    sections = extract_sections(html)
    return Filing(
        ticker=ticker,
        cik=cik,
        company_name=company_name,
        form_type=FormType.TEN_K,
        fiscal_year=financials.fiscal_year,
        fiscal_period="FY",
        filed_date=filing_ref.filed_date,
        accession_number=filing_ref.accession_number,
        period_end=financials.period_end,
        financials=financials,
        text_sections=sections,
    )


def main() -> int:
    print(f"=== Test du pipeline complet contre la vraie API SEC EDGAR ===")
    print(f"Ticker: {TICKER}")
    print(f"User-Agent: {USER_AGENT}")
    print()

    if "@" not in USER_AGENT:
        _fail("SEC_USER_AGENT ne contient pas d'email de contact -- SEC EDGAR va bloquer les requêtes (403).")
        return 1

    config = EdgarClientConfig(user_agent=USER_AGENT, timeout_seconds=30.0)
    client = EdgarClient(config)

    try:
        cik = CikLookup(client).resolve(TICKER)
        _ok(f"CIK résolu pour {TICKER}: {cik}")
    except (EdgarClientError, FilingNotFoundError) as exc:
        _fail(f"Résolution du CIK: {exc}")
        return 1

    try:
        filings = list_filings(client, cik, form_type="10-K", limit=2)
        _ok(f"{len(filings)} filing(s) 10-K trouvé(s), le plus récent: {filings[0].accession_number} ({filings[0].filed_date})")
    except (EdgarClientError, FilingNotFoundError) as exc:
        _fail(f"Listing des filings: {exc}")
        return 1

    try:
        current_filing = _build_filing(client, cik, filings[0], TICKER, TICKER)
        _ok(f"Filing courant construit: revenue={current_filing.financials.revenue}, "
            f"net_income={current_filing.financials.net_income}")
        _ok(f"Tags XBRL résolus: {current_filing.financials.resolved_tags}")
        _ok(f"Risk Factors trouvés: {current_filing.text_sections.item_1a_risk_factors is not None}")
        _ok(f"MD&A trouvé: {current_filing.text_sections.item_7_mdna is not None}")
    except Exception as exc:  # noqa: BLE001 -- report any failure clearly, this is a diagnostic script
        _fail(f"Construction du filing courant: {exc}")
        return 1

    prior_filing = None
    if len(filings) > 1:
        try:
            prior_filing = _build_filing(client, cik, filings[1], TICKER, TICKER)
            _ok(f"Filing précédent construit ({filings[1].accession_number})")
        except Exception as exc:  # noqa: BLE001
            _warn(f"Filing précédent non disponible: {exc}")
    else:
        _warn("Un seul 10-K disponible -- pas de comparaison YoY possible.")

    dictionary = None
    if DICTIONARY_PATH.exists():
        dictionary = load_lm_dictionary(DICTIONARY_PATH)
        _ok(f"Dictionnaire Loughran-McDonald chargé ({DICTIONARY_PATH.name})")
    else:
        _warn(f"Dictionnaire non trouvé à {DICTIONARY_PATH} -- sentiment indisponible.")

    report = build_report_data(current_filing, prior_filing, dictionary)

    for name, section in [
        ("Altman Z-Score", report.altman_z),
        ("Beneish M-Score", report.beneish_m),
        ("Piotroski F-Score", report.piotroski_f),
        ("Diff MD&A", report.mdna_diff),
        ("Diff Risk Factors", report.risk_factors_diff),
        ("Sentiment MD&A", report.mdna_sentiment),
        ("Sentiment Risk Factors", report.risk_factors_sentiment),
    ]:
        if section.available:
            _ok(f"{name}: calculé")
        else:
            _warn(f"{name}: indisponible -- {section.unavailable_reason}")

    html = render_html(report)
    save_pdf(html, OUTPUT_PDF)
    _ok(f"Rapport PDF généré: {OUTPUT_PDF} ({OUTPUT_PDF.stat().st_size} octets)")

    print()
    print("=== Terminé avec succès ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
