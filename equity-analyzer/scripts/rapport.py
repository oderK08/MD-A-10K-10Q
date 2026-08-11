"""
One ticker in, one PDF out.

  PAGE 1  Claude's reading of the latest earnings call, written against
          the consensus that quarter was measured on.
  PAGE 2  The same call taken apart: what was dodged, what was conceded,
          what carried forward looking value and was not in the press
          release. Dropped when the transcript isolates no Q&A, so a
          report without one is two pages rather than three.
  PAGE 3  The numbers that do not come from the call: the annual red
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

WHAT THE READING IS MEASURED AGAINST, and it is three things rather than
one.

  THE EPS CONSENSUS says what the QUARTER was expected to earn.

  THE PREVIOUS QUARTER'S COMMITMENTS say what the COMPANY said it would
  do. A quarter can beat the consensus while quietly doubling its capex
  envelope, and without this the reading reports that envelope as a
  level and never as a change (see report/guidance_sheet.py, and the
  real MSFT report that quoted a raised capex twice without once saying
  it had been raised).

  THE PRESS RELEASE says what was ALREADY PUBLIC before anyone spoke.
  Both passes need it for the same distinction: a figure already in the
  release has been read by everyone, the same figure volunteered under
  questioning has not. The Q&A pass in particular is told that what
  carried forward looking value and was NOT in the release is the most
  useful thing it can find, and it used to answer that from an
  assumption about a document it had never seen.

WHAT IT COSTS PER RUN
  SEC EDGAR      4 requests, plus 2 to 4 for the press release (its
                 filing index and the exhibit, once or twice if the
                 newest filing turns out to be another quarter). Free,
                 no key. Rate limited client side.
  Alpha Vantage  3 of the free tier's 25 daily requests (1 consensus,
                 1 transcript, 1 for the previous quarter's call; up to
                 2 more if that previous call is missing and the search
                 steps back, and 2 to 4 more only if the newest quarters
                 are not published yet). A transcript already fetched
                 comes from the disk cache and costs nothing.
  Anthropic      3 calls: the reading (roughly 10,000 input tokens and
                 900 output), the Q&A pass, and the baseline extraction
                 on a smaller budget. The last two are both optional and
                 neither can stop the report.

WHAT HAPPENS WHEN SOMETHING IS MISSING. Everything except the reading
itself degrades to a printed reason: no consensus, no 10-K, no MD&A, no
previous call each show up as a sentence explaining the gap. The reading
is the exception, because page 1 IS the reading: if the transcript or
the model cannot be had, this exits without writing a PDF rather than
producing a document whose first page apologises.
"""

from __future__ import annotations

import dataclasses
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
from equity_analyzer.data_layer.local_text_source import LocalTextSource, expected_filename
from equity_analyzer.data_layer.transcript_cache import CachedTranscriptSource, TranscriptCache
from equity_analyzer.data_layer.earnings_release import (
    announces_a_newer_quarter,
    list_earnings_8ks,
)
from equity_analyzer.data_layer.transcript_period import (
    PeriodLabelUnavailable,
    alpha_vantage_label,
    find_latest_available,
    next_label,
    previous_label,
    verify_against_declared,
)
from equity_analyzer.data_layer.transcript_source import (
    ChainedSource,
    EdgarExhibitSource,
    TranscriptRefused,
    TranscriptUnavailable,
    alpha_vantage_source,
)
from equity_analyzer.report import (
    ClaudeError,
    analyse_call,
    analyse_qa,
    build_call_report,
    render_html,
    save_pdf,
)
from equity_analyzer.data_layer.press_release import (
    as_prompt_block as press_release_block,
    fetch_press_release,
)
from equity_analyzer.report.guidance_sheet import as_prompt_block, extract_guidance
from equity_analyzer.sentiment import load_lm_dictionary

TICKER = os.environ.get("TICKER", "").strip().upper()
USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityAnalyzer/1.0 contact@example.com").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip()
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()

# One document. Page 1 the reading, page 2 the Q&A dissected, page 3 the
# numbers.
#
# THE BUDGET FOLLOWS THE DOCUMENT rather than being one number, and
# getting that wrong was a real regression. A report with no Q&A page has
# two pages of content, and one tight case overflows at natural size: a
# stale call AND a period mismatch warning together cost page 1 around
# sixty words of room. The fitter used to absorb that because the budget
# was two. Handed a flat budget of three it would see nothing to fix and
# hand back a document whose reading spills onto a sheet of its own, with
# the numbers pushed to a third.
#
# So a document without a Q&A page is still held to exactly two, which is
# what makes "two pages guaranteed, a third bounded" true rather than
# aspirational.
MAX_PAGES_WITH_QA = 3
MAX_PAGES_WITHOUT_QA = 2

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
    The most recent published call, in order of preference: a transcript
    committed to the repository, then Alpha Vantage, then the earnings
    8-K on EDGAR.

    THE LOCAL FILE COMES FIRST because it is free and deliberate. Alpha
    Vantage covers small caps badly and publishes the newest quarter
    late, which bites hardest during earnings season, so the escape
    hatch is to transcribe the webcast yourself and commit the text. It
    needs no terminal: create the file on github.com and relaunch.

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
    # A transcript committed to the repository is consulted BEFORE the
    # provider: it is free, and it is there because someone put it there
    # on purpose. Named after the fiscal quarter so it expires by itself
    # (see data_layer/local_text_source.py).
    local = LocalTextSource(CACHE_DIR)
    print(f"         (dépôt local attendu : transcripts/{expected_filename(ticker, start_label)})")

    source = CachedTranscriptSource(
        ChainedSource([local, alpha_vantage_source()]), TranscriptCache(CACHE_DIR)
    )

    if ALPHAVANTAGE_API_KEY or (CACHE_DIR / expected_filename(ticker, start_label)).is_file():
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
        _warn("ALPHAVANTAGE_API_KEY absent et aucun transcript déposé : "
              "seul le repli SEC 8-K est disponible.")

    call = EdgarExhibitSource().fetch(ticker, cik, client)
    # The exhibit route has no quarter parameter: it returns whatever the
    # newest earnings 8-K carries. Labelling that with the quarter we
    # asked Alpha Vantage for would be an assumption, so the label comes
    # back as the one EDGAR filed it under and `quarters_back` stays 0.
    return call, start_label, 0


# How far back the baseline search may walk. Deliberately shorter than
# the four quarters the main transcript search allows, for two reasons
# that point the same way.
#
# EACH STEP COSTS A PROVIDER REQUEST out of a daily budget of 25 that
# the report's own transcript and the consensus also draw on. Spending
# four of them on a nicety, in a run that has already fetched what it
# needs, is the wrong trade.
#
# AND A BASELINE DECAYS. Comparing against two quarters ago still says
# something useful, provided the prompt says so. Against a year ago the
# question "did this change today" has essentially no relationship to
# the answer, and a stale comparison presented confidently is worse than
# an absent one.
MAX_BASELINE_QUARTERS_BACK = 3


def _find_prior_call(source, ticker, cik, label, client):
    """
    The most recent call BEFORE the one being read, and how many
    quarters back it turned out to be.

    Walking back rather than giving up after one miss, because a single
    quarter the provider never published would otherwise cost the
    baseline entirely. Walking back only a little, and always returning
    the distance, because the distance changes what a difference means
    (see guidance_sheet.as_prompt_block).

    A REFUSAL STOPS THE WALK IMMEDIATELY, same as the main search:
    quota exhaustion applies to every subsequent request too, so
    continuing would spend the rest of the day's budget collecting the
    same error again.
    """
    quarter = previous_label(label)
    misses = []
    for attempt in range(MAX_BASELINE_QUARTERS_BACK):
        try:
            return source.fetch(ticker, cik, client, quarter=quarter), quarter, attempt + 1
        except TranscriptRefused:
            raise
        except TranscriptUnavailable as exc:
            misses.append(f"{quarter} ({exc})")
            quarter = previous_label(quarter)
    raise TranscriptUnavailable(
        f"aucun call sur les {MAX_BASELINE_QUARTERS_BACK} trimestres précédant "
        f"{label} : " + " ; ".join(misses)
    )


def _press_release_block(cik, label, client):
    """
    What was already public before the call, rendered for both passes.
    Never raises.

    WHY IT IS WORTH A FETCH. The Q&A pass is told that the most useful
    thing it can find is what carried forward looking value and was NOT
    in the press release, and it had never seen one: that section rested
    on an assumption about an unread document. The release is on EDGAR
    under Item 2.02, free, no key, no quota, so this costs two requests
    against a rate limit rather than anything scarce.

    Failure is a printed reason plus a block that tells both passes they
    CANNOT know what was already public, which is the part that matters.
    An absent block would let them assume a release and answer against
    the assumption.
    """
    try:
        release = fetch_press_release(client, cik, label)
    except Exception as exc:  # noqa: BLE001 -- a nicety must never cost the report
        _warn(f"Communiqué de résultats non apparié : les passes ne pourront pas "
              f"dire ce qui était déjà public ({exc})")
        return press_release_block(None, reason=f"non apparié à {label}")

    _ok(f"Communiqué {label} : {release.word_count} mots ({release.document})")
    return press_release_block(release)


def _prior_guidance_block(ticker, cik, label, client, company_name):
    """
    What the company committed to one quarter ago, rendered for the
    reading prompt. Never raises.

    WHY THIS IS ALLOWED TO FAIL QUIETLY. It is a second reference point,
    not the report. The quarter before the earliest one on file does not
    exist, a small cap's previous call may never have been published,
    and the daily provider budget can run out. In every one of those
    cases the reading is still worth writing against the consensus
    alone, so each failure becomes a printed reason plus a block that
    tells the model IT HAS NO BASELINE, which is the part that matters:
    an absent block would let it fill the gap from memory.

    COST, stated plainly because it is charged on every run: one more
    Alpha Vantage request out of the free tier's 25 (three total), and
    one more Claude call, on a smaller budget than the reading's. The
    disk cache absorbs the first of the two on a rerun.
    """
    # BROAD ON PURPOSE, and this is the one place in the script where
    # that is right. Everything below is a nicety: the report is fully
    # computable without it. Catching only the transcript exceptions
    # would leave a network error, a malformed cache entry or a provider
    # returning something unexpected free to abort a run that was one
    # step from writing a PDF, and paying for a fetched transcript to
    # then throw the report away is the worst outcome available here.
    try:
        source = CachedTranscriptSource(
            ChainedSource([LocalTextSource(CACHE_DIR), alpha_vantage_source()]),
            TranscriptCache(CACHE_DIR),
        )
        prior_call, previous, back = _find_prior_call(source, ticker, cik, label, client)
    except Exception as exc:  # noqa: BLE001 -- see above
        _warn(f"Aucun call antérieur récupérable : la lecture n'aura pas de base "
              f"de comparaison sur la guidance ({exc})")
        return as_prompt_block(None, reason="aucun call antérieur disponible")

    if back > 1:
        _warn(f"Le call {previous_label(label)} n'est pas disponible : base de "
              f"comparaison prise {back} trimestres en arrière ({previous}). "
              f"Un écart portera sur {back} trimestres, pas sur celui-ci.")

    try:
        sheet = extract_guidance(
            ticker, previous, prior_call.full_text,
            api_key=ANTHROPIC_API_KEY,
            company_name=company_name,
            verbatim=prior_call.verbatim,
            quarters_before=back,
            **({"model": ANTHROPIC_MODEL} if ANTHROPIC_MODEL else {}),
        )
    except Exception as exc:  # noqa: BLE001 -- see above
        _warn(f"Engagements de {previous} non extraits : {exc}")
        return as_prompt_block(None, reason=f"extraction de {previous} échouée")

    if sheet.is_empty:
        _warn(f"Aucun engagement chiffré trouvé dans le call {previous} : "
              f"rien à comparer.")
        return as_prompt_block(None, reason=f"aucun engagement chiffré en {previous}")

    _ok(f"Base de comparaison : {len(sheet.commitments)} engagement(s) chiffré(s) "
        f"pris en {previous}")
    return as_prompt_block(sheet)


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
    # 10-Q or 10-K that EDGAR indexes follows within days to weeks. In
    # that window the newest call exists and the newest periodic filing
    # is for the quarter before it, so anchoring on filings alone reads
    # a call one quarter old and says nothing about it.
    #
    # The test is the ORDER OF FILING, not a date comparison: an 8-K's
    # period_of_report is the date of the event, not a fiscal period end
    # (see earnings_release.announces_a_newer_quarter, and the real run
    # that got this wrong).
    start_label = filed_label
    stepped_forward = False
    try:
        recent_8k = list_earnings_8ks(client, cik, limit=1)[0]
        if announces_a_newer_quarter(
            recent_8k,
            filed_date=quarter_filing.filed_date,
            period_end=quarter_filing.period_end,
        ):
            start_label = next_label(filed_label)
            stepped_forward = True
            _ok(
                f"Résultats annoncés le {recent_8k.filed_date}, après le dépôt du "
                f"{quarter_filing.filed_date} : un trimestre de plus a été publié "
                f"mais pas encore déposé, recherche du call sur {start_label}"
            )
        else:
            _ok(
                f"Dernier communiqué de résultats du {recent_8k.filed_date} : celui "
                f"du trimestre déposé, rien de plus récent à chercher"
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
        print()
        print("        Tu peux en déposer un toi même, sans terminal : transcris le")
        print("        webcast (Whisper), puis sur github.com fais Add file → Create")
        print("        new file et colle le texte dans :")
        print(f"            equity-analyzer/transcripts/{expected_filename(TICKER, start_label)}")
        print("        Relance ensuite le workflow : ce fichier est lu avant le")
        print("        fournisseur et ne coûte aucun quota.")
        return 2

    # STALENESS IS MEASURED AGAINST THE NEWEST FILED QUARTER, not against
    # wherever the search happened to start. When the search stepped
    # forward to a quarter that turned out not to be published yet,
    # falling back one step lands on the newest filed quarter, which is
    # the current call and not a stale one. Reporting that as "one
    # quarter behind" would print a warning that is simply false, and a
    # false warning on page 1 is worse than no warning at all.
    stale_quarters = quarters_back - (1 if stepped_forward else 0)
    if stale_quarters > 0:
        _warn(
            f"Le call du trimestre déposé n'est pas encore publié : ceci est le "
            f"call disponible le plus récent, {stale_quarters} trimestre(s) en arrière."
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
    #
    # One anchor, one offset. `stale_quarters` already says where the
    # call sits relative to the newest FILED quarter, and it is negative
    # when the call is for a quarter EDGAR does not have yet. The
    # provider's list is newest first, so that offset addresses the
    # right line in both directions without a second anchor to keep in
    # step with this one.
    expectation = history = None
    expectations_reason = None
    try:
        expectations = fetch_earnings_expectations(TICKER)
        expectation = expectations.at(quarter_filing.period_end, stale_quarters)
        if expectation is None:
            expectations_reason = (
                f"aucune ligne de consensus pour le trimestre du call "
                f"(repère : trimestre clos le {quarter_filing.period_end}, "
                f"décalage {stale_quarters:+d})"
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

    # -- The Q&A pass, which runs FIRST --
    #
    # OPTIONAL BUT FIRST, and the order is the point. It becomes page 2,
    # and whether page 2 exists changes what page 1 is asked for: with an
    # inventory of the dodges on the next sheet, the reading is told not
    # to spend a fifth of a hard capped page repeating them in prose (see
    # call_analysis._DODGES_SECTION). That instruction has to be settled
    # before the reading is written, and the only honest way to know is
    # to have already run this.
    #
    # THE COST OF BEING WRONG THE OTHER WAY was measured on a real TSLA
    # run: the same dodged question appeared as a paragraph on page 1 and
    # as a row on page 2.
    #
    # A failure here is still not fatal. It downgrades to a printed
    # reason, page 1 is then written WITH its dodges section, and the
    # document comes out two pages instead of three: the reader loses the
    # inventory, never the finding. Running this first costs one wasted
    # call only when the reading afterwards fails, which aborts the run
    # anyway.
    # -- What the market already knew before anyone spoke --
    #
    # Fetched before both passes because both need it, and for the same
    # reason: to tell an information that was already public from one
    # that only came out of someone's mouth. See _press_release_block.
    press_release = _press_release_block(cik, label, client)

    qa_analysis = None
    if not (call.qa or "").strip():
        _warn("Session questions-réponses non isolée dans ce transcript : "
              "pas de page 2, les esquives restent dans la lecture.")
    else:
        try:
            qa_analysis = analyse_qa(
                TICKER, label, call.qa,
                api_key=ANTHROPIC_API_KEY,
                company_name=company_name,
                verbatim=call.verbatim,
                press_release=press_release,
                **({"model": ANTHROPIC_MODEL} if ANTHROPIC_MODEL else {}),
            )
            _ok(
                f"Q&A lue : {len(qa_analysis.dodged_questions)} esquive(s) dont "
                f"{len(qa_analysis.hard_dodges)} grave(s), "
                f"{len(qa_analysis.concessions)} concession(s), "
                f"{len(qa_analysis.implicit_guidance)} signal(aux) prospectif(s)"
            )
        except ClaudeError as exc:
            _warn(f"Lecture de la Q&A échouée, la lecture du call gardera "
                  f"ses esquives : {exc}")

    # -- The second baseline: what was promised a quarter ago --
    #
    # The consensus says what the QUARTER was expected to earn. It says
    # nothing about what the COMPANY said it would do, so until now a
    # capex envelope, a margin target or a revenue guide arrived with no
    # reference point and the reading could report a level but never a
    # change. Found on a real MSFT report, which quoted the capex twice
    # and never said it had moved.
    prior_guidance = _prior_guidance_block(TICKER, cik, label, client, company_name)

    # -- The reading --
    print(f"  ...   Lecture du call par Claude ({ANTHROPIC_MODEL or 'modèle par défaut'})")
    try:
        analysis = analyse_call(
            TICKER, label, call.full_text,
            api_key=ANTHROPIC_API_KEY,
            company_name=company_name,
            expectation=expectation,
            history=history or (),
            verbatim=call.verbatim,
            qa_page=qa_analysis is not None,
            prior_guidance=prior_guidance,
            press_release=press_release,
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
        quarters_back=max(stale_quarters, 0),
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

    report = dataclasses.replace(report, qa_analysis=qa_analysis)

    REPORTS_DIR.mkdir(exist_ok=True)
    output = REPORTS_DIR / f"{TICKER}.pdf"
    # Compacted to fit the budget this document actually has. With a Q&A
    # page that is a target with a net rather than a guarantee, because
    # page 2's length follows the session: the stylesheet tightens until
    # it fits and, if even the tightest step overruns, the longer render
    # is kept. Never a dropped finding.
    budget = MAX_PAGES_WITH_QA if qa_analysis is not None else MAX_PAGES_WITHOUT_QA
    pages = save_pdf(render_html(report), output, max_pages=budget)
    # The count is the one the PDF actually has, not the budget: the
    # fitter keeps the longest render rather than losing a row, so a
    # heavy session can legitimately come out at four and the log has to
    # say four.
    _ok(
        f"Rapport écrit : {output.relative_to(ROOT)} ({pages} pages"
        f"{'' if qa_analysis is not None else ', pas de Q&A isolée'})"
    )

    print()
    print("=" * 70)
    print(analysis.text)
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
