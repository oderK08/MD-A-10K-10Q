"""
Answers, for a list of tickers, whether building our own transcript
pipeline is worth it -- with measurements instead of guesses.

The reasoning this exists to settle: an earnings call transcript is
where the information actually is, transcribing one is cheap and easy,
and ACQUIRING THE AUDIO is the hard part because every issuer hosts its
webcast somewhere different. That is either a manageable handful of
vendor adapters or an endless treadmill, and which one it is depends
entirely on how the real distribution falls. Nobody can reason their way
to that. One run of this can measure it.

Per ticker, cheapest route first:
  1. Does the filer attach a transcript to its earnings 8-K? Free,
     already reachable, no crawling, no adapter. Done if yes.
  2. What does its IR site use? Vendor detected by signature, plus any
     direct media links, plus whether a registration wall is in the way.

Output is `transcript_coverage.csv`, one row per ticker, plus a
distribution printed at the end -- the actual decision input: how many
companies fall into each bucket.

TAKE THE FIRST RUN AS A DRAFT. The vendor signatures were written from
general knowledge of who the IR webcast vendors are, without being able
to reach a single one of these pages from the environment they were
written in. A vendor that never matches is a signature to fix, not a
population of companies that offers nothing. Read the "rien trouvé" rows
before concluding anything from the totals.

Universe is whatever TICKERS says, exactly like the main pipeline, so
pointing this at the Nasdaq-100 or at a personal watchlist is an input
and not a code change. Note the two populations behave very differently:
large caps have professional IR infrastructure and are the best covered
by paid APIs too, while the small caps where a DIY pipeline would earn
its keep are the ones most likely to come back gated or empty.
"""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from pathlib import Path

from equity_analyzer.data_layer import (
    CikLookup,
    EdgarClient,
    EdgarClientConfig,
    EdgarClientError,
    FilingNotFoundError,
)
from equity_analyzer.data_layer.webcast_discovery import PageFetcher, survey_ticker

TICKERS = [t.strip().upper() for t in os.environ.get("TICKERS", "").split(",") if t.strip()]
USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityAnalyzer/1.0 contact@example.com").strip()
# IR sites are ordinary corporate web servers. Identify the crawler and
# give a contact, the same courtesy SEC requires and for the same reason.
IR_USER_AGENT = os.environ.get("IR_USER_AGENT", f"EquityAnalyzerSurvey/1.0 ({USER_AGENT})")

ROOT = Path(__file__).parent.parent
OUTPUT_CSV = ROOT / "transcript_coverage.csv"

FIELDS = [
    "ticker", "cik", "verdict", "edgar_transcript_exhibit",
    "vendors", "registration_gated", "direct_audio_links",
    "ir_url", "ir_page_read", "ir_note", "fetch_routes", "error",
]


def main() -> int:
    if not TICKERS:
        print("TICKERS est vide. Renseigne la liste à sonder (ex: TICKERS=AAPL,MSFT,AAOI).")
        return 1

    if "@" not in USER_AGENT:
        print("SEC_USER_AGENT doit contenir un email de contact, sinon SEC EDGAR renvoie 403.")
        return 1

    print("=== Reconnaissance : où vit le transcript, et sous quel format ===")
    print(f"{len(TICKERS)} ticker(s) à sonder")
    print()

    client = EdgarClient(EdgarClientConfig(user_agent=USER_AGENT, timeout_seconds=30.0))
    fetcher = PageFetcher(user_agent=IR_USER_AGENT)
    lookup = CikLookup(client)

    rows = []
    for ticker in TICKERS:
        try:
            cik = lookup.resolve(ticker)
        except (EdgarClientError, FilingNotFoundError) as exc:
            print(f"  {ticker:<8} CIK introuvable: {exc}")
            rows.append({f: "" for f in FIELDS} | {"ticker": ticker, "verdict": "erreur",
                                                   "error": f"CIK: {exc}"})
            continue

        coverage = survey_ticker(ticker, cik, edgar_client=client, page_fetcher=fetcher)
        rows.append({
            "ticker": coverage.ticker,
            "cik": coverage.cik,
            "verdict": coverage.verdict,
            "edgar_transcript_exhibit": coverage.edgar_transcript_exhibit or "",
            "vendors": " | ".join(coverage.vendors),
            "registration_gated": "oui" if coverage.registration_gated else "",
            "direct_audio_links": " | ".join(coverage.direct_audio_links),
            "ir_url": coverage.ir_url or "",
            "ir_page_read": "oui" if coverage.ir_page_read else "",
            "ir_note": coverage.ir_note or "",
            "fetch_routes": " | ".join(coverage.fetch_routes),
            "error": coverage.error or "",
        })

        detail = (
            coverage.edgar_transcript_exhibit
            or " | ".join(coverage.vendors)
            or coverage.ir_note
            or "-"
        )
        print(f"  {ticker:<8} {coverage.verdict:<14} {detail}")

    with open(OUTPUT_CSV, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    unread = [r for r in rows if r["verdict"] == "IR non lue"]
    if unread:
        # Called out before the totals, because these rows are holes in
        # the survey and not measurements. Reading them as "these
        # companies offer nothing" is exactly the mistake this section
        # exists to prevent.
        print()
        print(f"=== {len(unread)} société(s) dont la page IR n'a PAS été lue ===")
        print("  Ce ne sont pas des résultats : la sonde n'a pas pu regarder.")
        print(f"  Motif du premier cas : {unread[0]['ir_note']}")
        print("  → Si le champ d'URL IR manque chez SEC, la liste de champs")
        print("    ci-dessus dit ce qui EST disponible pour retrouver l'adresse.")

    print()
    print("=== Distribution (c'est ça, la décision) ===")
    for verdict, count in Counter(r["verdict"] for r in rows).most_common():
        share = 100 * count / len(rows)
        print(f"  {verdict:<14} {count:>3}  ({share:.0f}%)")

    print()
    print("=== Prestataires rencontrés ===")
    vendors = Counter(
        vendor
        for row in rows
        for vendor in (row["vendors"].split(" | ") if row["vendors"] else [])
    )
    if vendors:
        for vendor, count in vendors.most_common():
            print(f"  {vendor:<26} {count:>3}")
        print()
        print(f"  {len(vendors)} adaptateur(s) à écrire pour couvrir cette liste.")
    else:
        # Stated rather than left as an empty section: with unvalidated
        # signatures, zero matches is far more likely to mean the
        # patterns are wrong than that no company uses a webcast vendor.
        print("  Aucun. Avec des signatures non validées, c'est presque")
        print("  certainement un problème de signatures et pas une absence réelle.")

    print()
    print(f"Détail complet dans {OUTPUT_CSV.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
