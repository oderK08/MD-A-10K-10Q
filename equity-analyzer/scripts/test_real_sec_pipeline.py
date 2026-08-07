"""
Runs the full equity_analyzer pipeline (Modules 1-5) against the REAL SEC
EDGAR API for a LIST of tickers spanning different sectors, to catalog
reliability gaps -- missing XBRL tags, failed text extraction, etc. --
across real, diverse filings rather than validating against just one
company (which is how the MSFT run surfaced the Item 1A extraction gap
this diagnostic mode exists to help fix).

Meant to be triggered from GitHub Actions (see
.github/workflows/test-real-sec-api.yml), which runs on GitHub's own
servers -- unlike the sandboxes this project was developed in, those have
normal internet access, so this is the first time the pipeline runs
against live data instead of the fixtures under tests/fixtures/.

Reads its ticker list and SEC User-Agent from environment variables so
the workflow's input boxes (filled in the GitHub Actions UI, no terminal
needed) can control them without editing this file.

Outputs:
- Two PDFs per ticker in rapports/: <TICKER>.pdf (the two-page
  main report) and <TICKER>_detail.pdf (the uncapped change detail)
- summary.csv: one row per ticker, one column per pipeline stage, so
  gaps across companies are visible at a glance
- debug/<TICKER>_plaintext.txt + occurrence hints, for tickers where
  Item 1A (or Item 7) extraction failed outright OR came back
  suspiciously short (< MIN_EXPECTED_SECTION_WORDS words -- a real
  legally-mandated disclosure is never a one-liner, so "found a string"
  isn't the same as "found the section": a real NVDA run once "found" a
  26-word item_7_mdna, identical across two different fiscal years,
  which is exactly this failure mode wearing a found=True disguise) --
  for diagnosing the regex against real filing text without needing
  direct network access here.

The opt-in AI summary (report/ai_summary.py, Module 6) is controlled
per-run by the workflow's "use_ai_summary" checkbox, not by editing this
file: the workflow only sets ANTHROPIC_API_KEY in this process's
environment when the box is checked, so a run where it's left unchecked
never makes a paid API call even if a key is configured as a repo
secret.
"""

from __future__ import annotations

import csv
import os
import re
import sys
import traceback
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
from equity_analyzer.data_layer.text_sections import html_to_text
from equity_analyzer.report import (
    attach_ai_summary,
    attach_theme_selection,
    build_report_data,
    render_detail_html,
    render_html,
    save_pdf,
)
from equity_analyzer.sentiment import load_lm_dictionary

DEFAULT_TICKERS = "AAPL,MSFT,GOOGL,AMZN,NVDA,JPM,XOM,JNJ,PG,KO,WMT,DIS,BA,CAT,NFLX"

TICKERS = [t.strip().upper() for t in os.environ.get("TICKERS", DEFAULT_TICKERS).split(",") if t.strip()]
USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityAnalyzer/1.0 contact@example.com").strip()
# Opt-in only (see report/ai_summary.py): the workflow's "use_ai_summary"
# checkbox controls whether this env var is even set -- if the box was
# left unchecked, GitHub Actions never exposes the secret to this
# process, so this stays empty regardless of whether a key is
# configured in the repo. attach_ai_summary() treats an empty/missing
# key exactly like "never asked for this" -- see its own docstring.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

ROOT = Path(__file__).parent.parent
DICTIONARY_PATH = ROOT / "data" / "Loughran-McDonald_MasterDictionary_1993-2025.csv"
REPORTS_DIR = ROOT / "rapports"
DEBUG_DIR = ROOT / "debug"
SUMMARY_CSV = ROOT / "summary.csv"

# A genuine Item 1A / Item 7 section is realistically always at least a
# few hundred words -- these are legally mandated, heavily lawyered
# disclosures, never a one-liner. Found-a-string but way too short is
# the same failure mode as found-nothing (a boundary-detection bug that
# grabbed a stray "item 7"-shaped phrase instead of the real section),
# just silent: `is not None` alone reports it as a success. This was
# missed on a real NVDA run -- item_7_mdna "found" but only 26 words,
# identical between two different fiscal years -- because the debug
# dump only fired on outright None.
MIN_EXPECTED_SECTION_WORDS = 200

SUMMARY_FIELDS = [
    "ticker", "cik", "revenue", "net_income",
    "risk_factors_found", "risk_factors_words", "mdna_found", "mdna_words",
    "altman_z", "beneish_m", "piotroski_f",
    "mdna_diff", "risk_factors_diff",
    "mdna_sentiment", "risk_factors_sentiment",
    "themes",
    "ai_summary",
    "error",
]


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [SKIP] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _dump_extraction_debug(ticker: str, label: str, html: str) -> None:
    """
    Saves the plain-text version of a filing (post html_to_text) when a
    section fails to extract, plus prints the surrounding context of
    every "1A"/"item 7" occurrence found -- so a real extraction gap can
    be diagnosed from the uploaded artifact without needing direct
    network access to the filing.
    """
    DEBUG_DIR.mkdir(exist_ok=True)
    plain_text = html_to_text(html)
    (DEBUG_DIR / f"{ticker}_{label}_plaintext.txt").write_text(plain_text[:500_000])

    needle = "1a" if label == "item1a" else "item 7"
    occurrences = list(re.finditer(re.escape(needle), plain_text, re.IGNORECASE))
    print(f"  [DEBUG] '{needle}' appears {len(occurrences)} time(s) in extracted plaintext for {ticker}:")
    for m in occurrences[:6]:
        context = plain_text[max(0, m.start() - 60):m.start() + 60].replace("\n", "\\n")
        print(f"    ...{context}...")


def _build_filing(client, cik, filing_ref, ticker) -> Filing:
    company_facts = client.fetch_company_facts(cik)
    financials = build_financial_period(
        company_facts,
        accession_number=filing_ref.accession_number,
        fiscal_year=(filing_ref.period_of_report or filing_ref.filed_date).year,
        fiscal_period="FY",
    )
    html = client.fetch_filing_document(cik, filing_ref.accession_number, filing_ref.primary_document)
    sections = extract_sections(html)

    if sections.item_1a_risk_factors is None:
        _dump_extraction_debug(ticker, "item1a", html)
    elif len(sections.item_1a_risk_factors.split()) < MIN_EXPECTED_SECTION_WORDS:
        _dump_extraction_debug(ticker, "item1a", html)
    if sections.item_7_mdna is None:
        _dump_extraction_debug(ticker, "item7", html)
    elif len(sections.item_7_mdna.split()) < MIN_EXPECTED_SECTION_WORDS:
        _dump_extraction_debug(ticker, "item7", html)

    return Filing(
        ticker=ticker,
        cik=cik,
        company_name=ticker,
        form_type=FormType.TEN_K,
        fiscal_year=financials.fiscal_year,
        fiscal_period="FY",
        filed_date=filing_ref.filed_date,
        accession_number=filing_ref.accession_number,
        period_end=financials.period_end,
        financials=financials,
        text_sections=sections,
    )


def run_for_ticker(client, dictionary, ticker: str) -> dict:
    row = {field: "" for field in SUMMARY_FIELDS}
    row["ticker"] = ticker

    try:
        cik = CikLookup(client).resolve(ticker)
        row["cik"] = cik
        _ok(f"CIK résolu: {cik}")
    except (EdgarClientError, FilingNotFoundError) as exc:
        _fail(f"Résolution du CIK: {exc}")
        row["error"] = f"CIK: {exc}"
        return row

    try:
        filings = list_filings(client, cik, form_type="10-K", limit=2)
    except (EdgarClientError, FilingNotFoundError) as exc:
        _fail(f"Listing des filings: {exc}")
        row["error"] = f"filings: {exc}"
        return row

    try:
        current_filing = _build_filing(client, cik, filings[0], ticker)
        row["revenue"] = current_filing.financials.revenue.value if current_filing.financials.revenue else ""
        row["net_income"] = current_filing.financials.net_income.value if current_filing.financials.net_income else ""
        risk_factors_text = current_filing.text_sections.item_1a_risk_factors
        mdna_text = current_filing.text_sections.item_7_mdna
        row["risk_factors_found"] = risk_factors_text is not None
        row["risk_factors_words"] = len(risk_factors_text.split()) if risk_factors_text else 0
        row["mdna_found"] = mdna_text is not None
        row["mdna_words"] = len(mdna_text.split()) if mdna_text else 0
        _ok(f"Filing courant construit (revenue={row['revenue']}, net_income={row['net_income']})")
    except Exception as exc:  # noqa: BLE001 -- diagnostic script, report every failure
        _fail(f"Construction du filing courant: {exc}")
        row["error"] = f"current filing: {exc}"
        traceback.print_exc()
        return row

    prior_filing = None
    if len(filings) > 1:
        try:
            prior_filing = _build_filing(client, cik, filings[1], ticker)
            _ok("Filing précédent construit")
        except Exception as exc:  # noqa: BLE001
            _warn(f"Filing précédent non disponible: {exc}")

    report = build_report_data(current_filing, prior_filing, dictionary)

    for key, section, name in [
        ("altman_z", report.altman_z, "Altman Z-Score"),
        ("beneish_m", report.beneish_m, "Beneish M-Score"),
        ("piotroski_f", report.piotroski_f, "Piotroski F-Score"),
        ("mdna_diff", report.mdna_diff, "Diff MD&A"),
        ("risk_factors_diff", report.risk_factors_diff, "Diff Risk Factors"),
        ("mdna_sentiment", report.mdna_sentiment, "Sentiment MD&A"),
        ("risk_factors_sentiment", report.risk_factors_sentiment, "Sentiment Risk Factors"),
    ]:
        if section.available:
            row[key] = "OK"
            _ok(f"{name}: calculé")
        else:
            row[key] = f"indisponible: {section.unavailable_reason}"
            _warn(f"{name}: indisponible -- {section.unavailable_reason}")

    # Opt-in only: the "use_ai_summary" workflow checkbox controls whether
    # ANTHROPIC_API_KEY is even set in this process's environment (see the
    # module-level comment). A ticker run never silently starts making paid
    # API calls just because a repo secret happens to exist.
    # Pass 1 of 2: pick the analyst-relevant sub-themes BEFORE writing
    # anything, so the summary is written over the selection rather than
    # over whatever happened to change most. Runs (with a documented
    # word-count fallback) even without a key, so the sub-theme table is
    # populated on a free run too.
    report = attach_theme_selection(report, api_key=ANTHROPIC_API_KEY or None)
    if report.theme_selection is not None and report.theme_selection.available:
        selection = report.theme_selection.value
        row["themes"] = f"{len(selection.headings)} ({'IA' if selection.ai_selected else 'repli'})"
        _ok(f"Sous-thématiques retenues: {selection.reason}")
    else:
        row["themes"] = "aucune"

    # Pass 2 of 2: the executive summary, written over that selection.
    if ANTHROPIC_API_KEY:
        report = attach_ai_summary(report, api_key=ANTHROPIC_API_KEY)
        if report.ai_summary.available:
            row["ai_summary"] = "OK"
            _ok("Synthèse IA: générée")
        else:
            row["ai_summary"] = f"indisponible: {report.ai_summary.unavailable_reason}"
            _warn(f"Synthèse IA: indisponible -- {report.ai_summary.unavailable_reason}")
    else:
        row["ai_summary"] = "non demandée"

    try:
        REPORTS_DIR.mkdir(exist_ok=True)
        # Main report: hard two-page budget, compacted if needed.
        save_pdf(render_html(report), REPORTS_DIR / f"{ticker}.pdf", max_pages=2)
        # Companion "détail": deliberately uncapped -- capping it would
        # mean dropping changed text.
        save_pdf(render_detail_html(report), REPORTS_DIR / f"{ticker}_detail.pdf")
        _ok(f"PDF générés: rapports/{ticker}.pdf (2 p.) + {ticker}_detail.pdf")
    except Exception as exc:  # noqa: BLE001
        _fail(f"Génération du PDF: {exc}")
        row["error"] = (row["error"] + "; " if row["error"] else "") + f"pdf: {exc}"

    return row


def main() -> int:
    print("=== Test du pipeline complet contre la vraie API SEC EDGAR ===")
    print(f"Tickers: {', '.join(TICKERS)}")
    print()

    if "@" not in USER_AGENT:
        _fail("SEC_USER_AGENT ne contient pas d'email de contact -- SEC EDGAR va bloquer les requêtes (403).")
        return 1

    config = EdgarClientConfig(user_agent=USER_AGENT, timeout_seconds=30.0)
    client = EdgarClient(config)

    dictionary = None
    if DICTIONARY_PATH.exists():
        dictionary = load_lm_dictionary(DICTIONARY_PATH)
        _ok(f"Dictionnaire Loughran-McDonald chargé ({DICTIONARY_PATH.name})")
    else:
        _warn(f"Dictionnaire non trouvé à {DICTIONARY_PATH} -- sentiment indisponible pour tous les tickers.")

    if ANTHROPIC_API_KEY:
        # Report the model actually in effect rather than assuming the
        # default: it's selectable per workflow run (ANTHROPIC_MODEL),
        # and the run log is the only place the reader can confirm which
        # one produced the summaries in the generated PDFs.
        from equity_analyzer.report.ai_summary import DEFAULT_MODEL

        model_in_use = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
        _ok(f"Synthèse IA activée (modèle : {model_in_use}) -- un appel Claude par ticker.")
    else:
        _warn("Synthèse IA non demandée (case décochée ou pas de clé) -- aucun appel Claude ne sera fait.")
    print()

    results = []
    for ticker in TICKERS:
        print(f"--- {ticker} ---")
        results.append(run_for_ticker(client, dictionary, ticker))
        print()

    with open(SUMMARY_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    print("=== Résumé ===")
    print(
        f"{'Ticker':<8}{'Revenue':<18}{'RiskFactors':<13}{'(mots)':<8}"
        f"{'MD&A':<7}{'(mots)':<8}{'Altman':<9}{'Beneish':<9}{'Piotroski':<10}"
    )
    for r in results:
        print(
            f"{r['ticker']:<8}"
            f"{str(r['revenue'])[:16]:<18}"
            f"{str(r['risk_factors_found']):<13}"
            f"{str(r['risk_factors_words']):<8}"
            f"{str(r['mdna_found']):<7}"
            f"{str(r['mdna_words']):<8}"
            f"{('OK' if r['altman_z'] == 'OK' else 'X'):<9}"
            f"{('OK' if r['beneish_m'] == 'OK' else 'X'):<9}"
            f"{('OK' if r['piotroski_f'] == 'OK' else 'X'):<10}"
        )

    n_ok = sum(1 for r in results if not r["error"])
    n_thin = sum(
        1 for r in results
        if not r["error"] and (
            (r["risk_factors_found"] and 0 < r["risk_factors_words"] < MIN_EXPECTED_SECTION_WORDS)
            or (r["mdna_found"] and 0 < r["mdna_words"] < MIN_EXPECTED_SECTION_WORDS)
        )
    )
    print()
    print(f"=== Terminé : {n_ok}/{len(results)} tickers traités sans erreur bloquante ===")
    if n_thin:
        print(
            f"=== ATTENTION : {n_thin} ticker(s) avec une section \"trouvée\" mais "
            f"suspicieusement courte (< {MIN_EXPECTED_SECTION_WORDS} mots) -- voir debug/ ==="
        )
    print(f"Détails complets dans {SUMMARY_CSV.name} (artifact 'resultats-pipeline')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
