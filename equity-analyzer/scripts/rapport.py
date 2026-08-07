"""
One ticker in, one two-page PDF out.

  PAGE 1  Claude's reading of the latest earnings call, written against
          the consensus that quarter was measured on.
  PAGE 2  The numbers that do not come from the call: the annual red
          flags (Altman, Beneish, Piotroski) and the Loughran-McDonald
          tone of the call and of the quarter's MD&A.

WHICH QUARTER IS "THE LATEST" is answered by EDGAR, not guessed. The
transcript provider labels calls by the ISSUER's fiscal calendar, and
EDGAR already carries that calendar on every filing (`fy`/`fp`), so a
June-year filer and a January-year filer both work with no special
casing and no fiscal table to maintain. Deriving the quarter from the
calendar instead would be right for December-year filers and silently
wrong for Apple, NVIDIA, Micron and Microsoft, which is to say for most
of the names this tool gets pointed at.

And the newest quarter ON FILE is not always the newest quarter WITH a
transcript: a company can file its 10-Q days after reporting, before the
provider has published the call. The search walks back until it finds
one and the report says how far back it had to go, rather than passing
an older call off as the current one.

WHAT IT COSTS PER RUN
  SEC EDGAR      4 requests, free, no key. Rate limited client side.
  Alpha Vantage  2 of the free tier's 25 daily requests (1 consensus,
                 1 transcript; 2 to 4 more only if the newest quarters
                 are not published yet). A transcript already fetched
                 comes from the disk cache and costs nothing.
  Anthropic      1 call, roughly 10,000 input tokens and 900 output.

WHAT HAPPENS WHEN SOMETHING IS MISSING. Everything except the reading
itself degrades to a printed reason: no consensus, no 10-K, no MD&A each
show up on the page as a sentence explaining the gap. The reading is the
exception, because page 1 IS the reading: if the transcript or the model
cannot be had, this exits without writing a PDF rather than producing a
two page document whose first page apologises.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from equity_analyzer.data_layer import (
    CikLookup,
    EdgarClient,
    EdgarClientConfig,
    EdgarClientError,
    Filing,
    FilingNotFoundError,
    FormType,
    build_financial_period,
    extract_sections,
    filing_index_url,
    latest_reported_period,
    list_filings,
)
from equity_analyzer.data_layer.alpha_vantage import RETRY_AFTER_REFUSAL_SECONDS
from equity_analyzer.data_layer.earnings_expectations import (
    ExpectationsUnavailable,
    fetch_earnings_expectations,
)
from equity_analyzer.data_layer.transcript_cache import CachedTranscriptSource, TranscriptCache
from equity_analyzer.data_layer.earnings_release import list_earnings_8ks
from equity_analyzer.data_layer.transcript_period import (
    PeriodLabelUnavailable,
    alpha_vantage_label,
    find_latest_available,
    next_label,
    verify_against_declared,
)
from equity_analyzer.data_layer.transcript_source import (
    EdgarExhibitSource,
    TranscriptRefused,
    TranscriptUnavailable,
    alpha_vantage_source,
)
from equity_analyzer.report import (
    ClaudeError,
    analyse_call,
    build_call_report,
    render_html,
    save_pdf,
)
from equity_analyzer.sentiment import load_lm_dictionary

TICKER = os.environ.get("TICKER", "").strip().upper()
USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityAnalyzer/1.0 contact@example.com").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip()
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "transcripts"
REPORTS_DIR = ROOT / "rapports"
DICTIONARY_PATH = ROOT / "data" / "Loughran-McDonald_MasterDictionary_1993-2025.csv"



def _ok(message: str) -> None:
    print(f"  [OK]   {message}")


def _warn(message: str) -> None:
    print(f"  [SKIP] {message}")


def _fail(message: str) -> None:
    print(f"  [FAIL] {message}")


def _build_filing(client, cik, ref, ticker, company_name, facts, with_text: bool) -> Filing:
    """
    One filing, with its XBRL figures and optionally its text.

    `with_text=False` is for the two 10-Ks fetched purely to feed the
    annual red flag models: those scores come entirely from XBRL, so
    downloading the filing document would pull tens of megabytes of HTML
    per company for text nothing reads.

    `fiscal_year` / `fiscal_period` are deliberately not passed to
    `build_financial_period`: it reads them off the filing's own XBRL
    labels, which is the only source that is right for a filer whose
    fiscal year does not end in December.
    """
    financials = build_financial_period(facts, accession_number=ref.accession_number)
    sections = None
    if with_text:
        html = client.fetch_filing_document(cik, ref.accession_number, ref.primary_document)
        sections = extract_sections(html)

    return Filing(
        ticker=ticker,
        cik=cik,
        company_name=company_name,
        form_type=FormType(ref.form_type),
        fiscal_year=financials.fiscal_year or (ref.period_of_report or ref.filed_date).year,
        fiscal_period=financials.fiscal_period or "FY",
        filed_date=ref.filed_date,
        accession_number=ref.accession_number,
        period_end=financials.period_end,
        financials=financials,
        text_sections=sections,
    )


def _fetch_transcript(ticker, cik, start_label, client):
    """
    The most recent published call, from Alpha Vantage, falling back to
    EDGAR when the provider will not answer.

    A REFUSAL IS RETRIED ONCE BEFORE GIVING UP ON THE PROVIDER. Alpha
    Vantage answers a burst limit ("please consider spreading out your
    free API requests more sparingly") and an exhausted daily budget
    with the same HTTP 200 and overlapping prose, so they cannot be told
    apart by reading the message. They demand opposite reactions: the
    first clears in seconds, the second lasts until tomorrow. Waiting
    once and asking again is the only honest response to that ambiguity.
    A burst limit then succeeds; a spent budget fails identically and
    costs one wait.

    THE EDGAR FALLBACK IS NOT DECORATION. Without a second route, one
    exhausted quota means no report at all. A minority of issuers attach
    their prepared remarks or the full call to their earnings 8-K, and
    for those the document is free, already reachable with the client
    this script holds, and is the genuine article straight from the
    company. It is a fallback and not the primary route because most
    issuers never file one, and because it always returns the newest
    earnings 8-K rather than a requested quarter.

    Returns (transcript, label, quarters_back).
    """
    source = CachedTranscriptSource(alpha_vantage_source(), TranscriptCache(CACHE_DIR))

    if ALPHAVANTAGE_API_KEY:
        # No explicit pause between attempts: every request through this
        # source already goes through the shared provider throttle (see
        # data_layer/alpha_vantage.py), so spacing them here as well
        # would be two mechanisms doing one job and drifting apart.
        for attempt in (1, 2):
            try:
                call, label, back = find_latest_available(source, ticker, cik, start_label)
                print(f"         ({source.hits} depuis le cache, {source.fetches} appel(s) API)")
                return call, label, back
            except TranscriptRefused as exc:
                _warn(f"Alpha Vantage a refusé : {exc}")
                if attempt == 1:
                    _warn(
                        f"Un débit trop rapide et un quota épuisé se ressemblent : "
                        f"nouvelle tentative dans {RETRY_AFTER_REFUSAL_SECONDS:.0f} s."
                    )
                    time.sleep(RETRY_AFTER_REFUSAL_SECONDS)
                    continue
                _warn("Deuxième refus : le quota du jour est probablement épuisé.")
            except TranscriptUnavailable as exc:
                _warn(f"Pas de transcript chez Alpha Vantage : {exc}")
            break
        _warn("Repli sur le 8-K de résultats déposé chez SEC.")
    else:
        _warn("ALPHAVANTAGE_API_KEY absent : seul le repli SEC 8-K est disponible.")

    call = EdgarExhibitSource().fetch(ticker, cik, client)
    # The exhibit route has no quarter parameter: it returns whatever the
    # newest earnings 8-K carries. Labelling that with the quarter we
    # asked Alpha Vantage for would be an assumption, so the label comes
    # back as the one EDGAR filed it under and `quarters_back` stays 0.
    return call, start_label, 0


def main() -> int:
    if not TICKER:
        print("TICKER est vide. Indique le ticker à analyser.")
        return 1
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY absent. C'est cette clé qui écrit la page 1.")
        return 1

    print(f"=== {TICKER} : rapport sur le dernier earnings call ===")
    client = EdgarClient(EdgarClientConfig(user_agent=USER_AGENT, timeout_seconds=30.0))

    # -- Which company, and which quarter it last filed --
    try:
        cik = CikLookup(client).resolve(TICKER)
        company_name = client.fetch_submissions(cik).get("name") or TICKER
        _ok(f"{company_name} (CIK {cik})")
    except (EdgarClientError, FilingNotFoundError) as exc:
        _fail(f"Ticker introuvable chez SEC : {exc}")
        return 1

    try:
        # 10-Q OR 10-K. A fiscal year has four quarters but only three
        # 10-Qs: the fourth is reported inside the 10-K. Asking for the
        # newest 10-Q skips one quarter in four, silently, for every
        # company (see cik_lookup.latest_reported_period).
        ref = latest_reported_period(client, cik)
        facts = client.fetch_company_facts(cik)
        quarter_filing = _build_filing(
            client, cik, ref, TICKER, company_name, facts, with_text=True
        )
        filed_label = alpha_vantage_label(
            quarter_filing.fiscal_year, quarter_filing.fiscal_period
        )
        _ok(
            f"Dernier trimestre déposé : {quarter_filing.fiscal_period} "
            f"{quarter_filing.fiscal_year} (clos le {quarter_filing.period_end}, "
            f"{quarter_filing.form_type.value}), repère fournisseur {filed_label}"
        )
    except (EdgarClientError, FilingNotFoundError, PeriodLabelUnavailable, ValueError) as exc:
        _fail(f"Impossible de déterminer le dernier trimestre : {exc}")
        return 1

    # -- Has the company REPORTED a quarter it has not yet FILED? --
    #
    # The earnings call happens on the day of the press release; the
    # 10-Q or 10-K that EDGAR indexes follows two to six weeks later. In
    # that window the newest call exists and the newest periodic filing
    # is for the quarter before it, so anchoring on filings alone reads
    # a call one quarter old and says nothing about it. The earnings
    # 8-K (Item 2.02) is filed the same day as the release, so it is the
    # cheapest honest signal that a newer quarter has been reported.
    start_label = filed_label
    consensus_anchor = quarter_filing.period_end
    try:
        recent_8k = list_earnings_8ks(client, cik, limit=1)[0]
        reported_end = recent_8k.period_of_report
        if reported_end is not None and reported_end > quarter_filing.period_end:
            start_label = next_label(filed_label)
            consensus_anchor = reported_end
            _ok(
                f"Résultats publiés le {recent_8k.filed_date} pour le trimestre clos "
                f"le {reported_end}, pas encore déposé : recherche du call sur "
                f"{start_label}"
            )
    except (EdgarClientError, FilingNotFoundError, IndexError, ValueError) as exc:
        _warn(f"Pas de 8-K de résultats exploitable, on s'en tient au dépôt : {exc}")

    # -- The annual filings the red flags are computed on. Never the
    #    quarter above: all three are annual models (see report_data). --
    annual_filing = prior_annual_filing = None
    try:
        annual_refs = list_filings(client, cik, form_type="10-K", limit=2)
        annual_filing = _build_filing(
            client, cik, annual_refs[0], TICKER, company_name, facts, with_text=False
        )
        if len(annual_refs) > 1:
            prior_annual_filing = _build_filing(
                client, cik, annual_refs[1], TICKER, company_name, facts, with_text=False
            )
        _ok(f"Base annuelle des red flags : 10-K exercice {annual_filing.fiscal_year}")
    except (EdgarClientError, FilingNotFoundError, ValueError) as exc:
        _warn(f"Pas de 10-K exploitable, red flags non calculés : {exc}")

    # -- The call itself --
    #
    # DELIBERATELY BEFORE THE CONSENSUS, and the order is load bearing in
    # two ways. The daily budget is 25 requests shared between the two,
    # and they are not equally important: without a transcript there is
    # no page 1 and no report, while a missing consensus degrades to a
    # printed sentence. The scarce resource goes to the critical request
    # first. And the transcript is what tells us WHICH quarter the call
    # is for, so asking for the consensus afterwards means asking for the
    # right quarter once instead of asking for the newest and correcting.
    try:
        call, label, quarters_back = _fetch_transcript(TICKER, cik, start_label, client)
    except TranscriptUnavailable as exc:
        _fail(f"Aucun transcript récupérable : {exc}")
        return 2

    if quarters_back:
        _warn(
            f"Le call du trimestre déposé n'est pas encore publié : ceci est le "
            f"call disponible le plus récent, {quarters_back} trimestre(s) en arrière."
        )
    _ok(f"Transcript {label} : {call.word_count} mots, source {call.source}")

    # The label came from EDGAR, but the provider may index a quarter
    # differently, and a wrong pairing is invisible in the output. The
    # company states the period out loud in the opening seconds of every
    # call, so the check is free.
    period_warning = verify_against_declared(label, call.full_text)
    if period_warning:
        _warn(f"ATTENTION : {period_warning}")

    # -- What the market expected, for the quarter of the call above --
    expectation = history = None
    expectations_reason = None
    try:
        expectations = fetch_earnings_expectations(TICKER)
        expectation = expectations.at(consensus_anchor, quarters_back)
        if expectation is None:
            expectations_reason = (
                f"aucune ligne de consensus pour le trimestre du call "
                f"(clos le {consensus_anchor}"
                + (f", {quarters_back} trimestre(s) plus tôt)" if quarters_back else ")")
            )
            _warn(expectations_reason)
        else:
            history = expectations.history_before(expectation)
            _ok(
                f"Consensus : attendu {expectation.estimated_eps}, "
                f"publié {expectation.reported_eps} ({expectation.verdict})"
            )
    except ExpectationsUnavailable as exc:
        expectations_reason = str(exc)
        _warn(f"Consensus indisponible : {exc}")

    # -- The reading --
    print(f"  ...   Lecture du call par Claude ({ANTHROPIC_MODEL or 'modèle par défaut'})")
    try:
        analysis = analyse_call(
            TICKER, label, call.full_text,
            api_key=ANTHROPIC_API_KEY,
            company_name=company_name,
            expectation=expectation,
            history=history or (),
            **({"model": ANTHROPIC_MODEL} if ANTHROPIC_MODEL else {}),
        )
    except ClaudeError as exc:
        _fail(f"La lecture a échoué : {exc}")
        print("        Le transcript est en cache : relancer ne recoûtera pas de quota.")
        return 2
    _ok(f"Lecture écrite par {analysis.model} ({len(analysis.text.split())} mots)")

    # -- The tone, and the document --
    dictionary = None
    try:
        dictionary = load_lm_dictionary(DICTIONARY_PATH)
    except Exception as exc:  # noqa: BLE001 -- absence of the file is the expected case
        _warn(f"Dictionnaire Loughran-McDonald illisible, tonalité non calculée : {exc}")

    report = build_call_report(
        TICKER, company_name, cik, call, analysis,
        call_quarter=label,
        quarters_back=quarters_back,
        period_warning=period_warning,
        expectation=expectation,
        expectations_reason=expectations_reason,
        expectations_history=history or (),
        quarter_filing=quarter_filing,
        annual_filing=annual_filing,
        prior_annual_filing=prior_annual_filing,
        lm_dictionary=dictionary,
        source_filing_url=filing_index_url(cik, quarter_filing.accession_number),
    )

    REPORTS_DIR.mkdir(exist_ok=True)
    output = REPORTS_DIR / f"{TICKER}.pdf"
    save_pdf(render_html(report), output, max_pages=2)
    _ok(f"Rapport écrit : {output.relative_to(ROOT)}")

    print()
    print("=" * 70)
    print(analysis.text)
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
