"""
One ticker in, the latest earnings call read and analysed out.

WHY THERE IS NO DIFF HERE. The rest of this project compares a filing
against the previous quarter's, because filings are largely copy-pasted
and the edits are the signal. The first live comparison of two Microsoft
calls came back at 7% sentence overlap: management writes a fresh script
every quarter, so a diff reports that almost everything changed -- true,
and worthless. Reading the call is the right tool for a call.

WHICH QUARTER IS "THE LATEST" is answered by EDGAR, not guessed. The
transcript provider labels by the issuer's fiscal calendar, and EDGAR
already carries that calendar on every filing (`fy`/`fp`), so a
June-year filer and a January-year filer both work with no special
casing and no fiscal table to maintain.

And the newest quarter ON FILE is not always the newest quarter WITH a
transcript -- AAOI had its Q2 2026 filed the same week it reported and
the provider did not have that call yet -- so the search walks back
until it finds one, and says how far back it had to go.

Costs 1 transcript request (2-4 if the newest quarters are not published
yet), plus one Claude call. Transcripts already fetched come from the
cache and cost nothing.
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
    FilingNotFoundError,
)
from equity_analyzer.data_layer.transcript_cache import CachedTranscriptSource, TranscriptCache
from equity_analyzer.data_layer.transcript_period import (
    PeriodLabelUnavailable,
    alpha_vantage_label,
    find_latest_available,
    verify_against_declared,
)
from equity_analyzer.data_layer.transcript_source import (
    TranscriptRefused,
    TranscriptUnavailable,
    alpha_vantage_source,
)
from equity_analyzer.report.ai_summary import AISummaryError
from equity_analyzer.report.call_analysis import analyse_call

TICKER = os.environ.get("TICKER", "").strip().upper()
USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityAnalyzer/1.0 contact@example.com").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip()

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "transcripts"
OUTPUT = ROOT / "dernier_call.md"

# Five requests a minute on the free tier. Only matters when walking
# back through unpublished quarters.
PAUSE_SECONDS = 13.0


def _resolve_latest_period(client, cik: str) -> tuple:
    from equity_analyzer.data_layer.cik_lookup import latest_quarterly_pair
    from equity_analyzer.data_layer.xbrl_normalizer import build_financial_period

    pair = latest_quarterly_pair(client, cik)
    facts = client.fetch_company_facts(cik)
    period = build_financial_period(facts, pair.current.accession_number)
    label = alpha_vantage_label(period.fiscal_year, period.fiscal_period)
    return label, f"{period.fiscal_period} {period.fiscal_year} (clos le {period.period_end})"


def main() -> int:
    if not TICKER:
        print("TICKER est vide. Indique le ticker à analyser.")
        return 1
    if not os.environ.get("ALPHAVANTAGE_API_KEY", "").strip():
        print("ALPHAVANTAGE_API_KEY absent. Ajoute-le en secret du dépôt.")
        return 1
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY absent. C'est cette clé qui fait l'analyse.")
        return 1

    print(f"=== {TICKER} : dernier earnings call ===")
    client = EdgarClient(EdgarClientConfig(user_agent=USER_AGENT, timeout_seconds=30.0))

    try:
        cik = CikLookup(client).resolve(TICKER)
    except (EdgarClientError, FilingNotFoundError) as exc:
        print(f"CIK introuvable pour {TICKER} : {exc}")
        return 1

    try:
        start_label, described = _resolve_latest_period(client, cik)
    except (EdgarClientError, FilingNotFoundError, PeriodLabelUnavailable, ValueError) as exc:
        print(f"Impossible de déterminer le dernier trimestre : {exc}")
        return 1

    print(f"Dernier trimestre déposé : {described}")

    source = CachedTranscriptSource(alpha_vantage_source(), TranscriptCache(CACHE_DIR))
    try:
        call, label, stale = find_latest_available(
            source, TICKER, cik, start_label, pause=lambda: time.sleep(PAUSE_SECONDS)
        )
    except TranscriptRefused as exc:
        print(f"Le fournisseur a refusé : {exc}")
        print("→ Rien à conclure sur la disponibilité. Vérifier le quota du jour.")
        return 2
    except TranscriptUnavailable as exc:
        print(f"Aucun transcript trouvé : {exc}")
        return 2

    if stale:
        # Stated rather than passing an older call off as the latest: a
        # quarter-old transcript is still useful, a quarter-old
        # transcript believed to be current is not.
        print(f"Le call du trimestre déposé n'est pas encore publié chez le fournisseur.")
    print(f"Transcript récupéré : {label}, {call.word_count} mots")

    warning = verify_against_declared(label, call.full_text)
    if warning:
        print(f"!! ATTENTION : {warning}")

    print(f"Analyse par Claude ({ANTHROPIC_MODEL or 'modèle par défaut'})…")
    try:
        analysis = analyse_call(
            TICKER, label, call.full_text,
            api_key=ANTHROPIC_API_KEY,
            **({"model": ANTHROPIC_MODEL} if ANTHROPIC_MODEL else {}),
        )
    except AISummaryError as exc:
        print(f"L'analyse a échoué : {exc}")
        print("Le transcript est quand même en cache, l'analyse peut être relancée.")
        return 2

    header = [
        f"# {TICKER} — earnings call {label}",
        "",
        f"Transcript intégral : {call.word_count} mots, source {call.source}.",
        f"Dernier trimestre déposé chez SEC : {described}.",
        f"Analyse : {analysis.model}.",
        "",
    ]
    if stale:
        header += [
            f"> Le call du trimestre déposé n'est pas encore publié. Ceci est le "
            f"call disponible le plus récent, {stale} trimestre(s) en arrière.",
            "",
        ]
    if warning:
        header += [f"> **Attention** — {warning}", ""]

    OUTPUT.write_text("\n".join(header + [analysis.text, ""]))

    print()
    print("=" * 70)
    print(analysis.text)
    print("=" * 70)
    print()
    print(f"Écrit dans {OUTPUT.name} — transcript en cache dans {CACHE_DIR.name}/ "
          f"({source.hits} depuis le cache, {source.fetches} appel(s) API)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
