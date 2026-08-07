"""
Measures, per company, what Alpha Vantage's `quarter` parameter actually
means.

The parameter looks calendar-shaped ("2026Q2"), but a live request for
Microsoft 2026Q2 returned the call for the quarter that ended in
DECEMBER 2025 -- Microsoft's fiscal Q2. So the label follows the
issuer's fiscal calendar. On a watchlist with three different fiscal
year-ends among six names, that is not a detail.

WHY IT HAS TO BE MEASURED. The report compares two consecutive
quarters. A mapping that is off by two quarters for a June-year filer,
or by nearly a full label-year for a January-year filer, produces no
crash and no empty section: the tool diffs two periods that do not
belong together and presents the result with complete confidence. It is
the most dangerous class of bug this project can have, and the only
defence is to check rather than assume.

HOW. For each ticker, ask for one quarter, then read back the period the
company NAMES in the opening seconds of its own call. Requested versus
declared is the mapping. No fiscal-year table is hardcoded anywhere --
the companies tell us, every quarter, out loud.

Costs one request per ticker.
"""

from __future__ import annotations

import os
import sys
import time

from equity_analyzer.data_layer.declared_period import read_declared_period
from equity_analyzer.data_layer.transcript_source import (
    TranscriptUnavailable,
    alpha_vantage_source,
)

TICKERS = [t.strip().upper() for t in os.environ.get("TICKERS", "").split(",") if t.strip()]
QUARTER = os.environ.get("PROBE_QUARTER", "2026Q1").strip()
# The free tier allows five requests a minute. Six tickers back to back
# would trip it, and a rate-limit refusal here would read as a company
# with no transcript -- exactly the confusion this script exists to
# avoid making elsewhere.
PAUSE_SECONDS = float(os.environ.get("PROBE_PAUSE_SECONDS", "13"))


def main() -> int:
    if not TICKERS:
        print("TICKERS est vide.")
        return 1
    if not os.environ.get("ALPHAVANTAGE_API_KEY", "").strip():
        print("ALPHAVANTAGE_API_KEY absent.")
        return 1

    print("=== Que signifie vraiment le paramètre `quarter` ? ===")
    print(f"Demande de {QUARTER} pour {len(TICKERS)} société(s)")
    print(f"Coût : {len(TICKERS)} requêtes sur les 25 du jour")
    print()

    source = alpha_vantage_source()
    rows = []

    for index, ticker in enumerate(TICKERS):
        if index:
            time.sleep(PAUSE_SECONDS)
        try:
            call = source.fetch(ticker, "", quarter=QUARTER)
        except TranscriptUnavailable as exc:
            print(f"  {ticker:<6} INDISPONIBLE : {exc}")
            rows.append((ticker, QUARTER, None, None, str(exc)))
            continue

        declared = read_declared_period(call.full_text)
        if declared is None:
            print(f"  {ticker:<6} {call.word_count:>6} mots — période non déclarée dans l'ouverture")
            rows.append((ticker, QUARTER, None, None, "période non lisible"))
            continue

        agrees = declared.label == QUARTER
        kind = "fiscal" if declared.says_fiscal else "civil"
        flag = "=" if agrees else "≠"
        print(f"  {ticker:<6} {call.word_count:>6} mots — demandé {QUARTER} {flag} déclaré "
              f"{declared.label} ({kind})   « {declared.phrase} »")
        rows.append((ticker, QUARTER, declared.label, kind, ""))

    print()
    print("=== Ce qu'il faut en retenir ===")
    mismatched = [r for r in rows if r[2] and r[2] != r[1]]
    fiscal_filers = [r for r in rows if r[3] == "fiscal"]

    if mismatched:
        print(f"  {len(mismatched)} société(s) renvoient un trimestre différent de celui demandé :")
        for ticker, asked, got, _, _ in mismatched:
            print(f"    {ticker:<6} demandé {asked} → reçu {got}")
        print("  → Le paramètre n'est pas interprété comme une date civile pour")
        print("    ces sociétés. Il faut traduire avant d'apparier deux trimestres.")
    else:
        print("  Toutes les sociétés jointes renvoient le trimestre demandé.")

    if fiscal_filers:
        print()
        print(f"  {len(fiscal_filers)} société(s) qualifient leur trimestre de « fiscal » :")
        print(f"    {', '.join(r[0] for r in fiscal_filers)}")
        print("  → Ce sont celles dont l'exercice ne suit pas l'année civile. Apparier")
        print("    leur trimestre avec celui d'un déposant calendaire compare deux")
        print("    périodes différentes, sans que rien ne le signale.")

    unreadable = [r for r in rows if r[4]]
    if unreadable:
        print()
        print(f"  {len(unreadable)} société(s) sans mesure — ce ne sont PAS des résultats :")
        for ticker, _, _, _, why in unreadable:
            print(f"    {ticker:<6} {why[:90]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
