"""
Drop a transcript you obtained yourself into the cache, so the report
can read a call the provider does not have.

WHY THIS EXISTS. Alpha Vantage covers large caps well and small caps
badly, and it publishes the newest quarter with a lag that bites hardest
during earnings season, which is exactly when the call matters most. The
honest DIY route is to transcribe the webcast yourself: an hour of
scripted business English is well within what a Whisper-class model
handles, for cents. This script is the seam that lets the result reach
the pipeline.

WHAT IT DOES NOT DO, and the distinction is legal rather than technical:
it does not fetch anything. Transcribing a public call you were invited
to listen to is one thing; copying a transcript site's text is another,
and this project does not provide the second (see transcript_source.py).

HOW IT REACHES THE REPORT. There is no special code path. The cache is
already the first thing `CachedTranscriptSource` looks at, so a file
sitting in `transcripts/` is used before any request goes out, and the
run then costs zero provider quota. The file is plain JSON on disk on
purpose: it survives being committed to the repository, and a reader who
wants to check a quotation can open it.

USAGE

    TICKER=UBER QUARTER=2026Q2 SOURCE_FILE=uber-q2.txt \\
        python scripts/importer_transcript.py

Optional:
    CALL_DATE=2026-08-05      the day of the call, ISO format
    SOURCE="Whisper large-v3" how it was produced, printed in the report
    VERBATIM=1                ONLY if this is the company's own written
                              record rather than a machine transcription
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from equity_analyzer.data_layer.transcript_cache import TranscriptCache
from equity_analyzer.data_layer.transcript_source import (
    CallTranscript,
    split_prepared_from_qa,
)

TICKER = os.environ.get("TICKER", "").strip().upper()
QUARTER = os.environ.get("QUARTER", "").strip().upper()
SOURCE_FILE = os.environ.get("SOURCE_FILE", "").strip()
CALL_DATE = os.environ.get("CALL_DATE", "").strip()
SOURCE = os.environ.get("SOURCE", "").strip()
VERBATIM = os.environ.get("VERBATIM", "").strip() == "1"

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "transcripts"

# Below this, whatever was pasted in is not an earnings call. A real one
# runs six to twelve thousand words; a few hundred means a truncated
# file, a summary, or the wrong document. Refusing here is far better
# than refusing after the model has read it, and infinitely better than
# not refusing at all.
MIN_WORDS = 1500


def main() -> int:
    if not (TICKER and QUARTER and SOURCE_FILE):
        print(__doc__.split("USAGE")[1].strip())
        return 1

    if not (len(QUARTER) == 6 and QUARTER[4] == "Q" and QUARTER[:4].isdigit()
            and QUARTER[5] in "1234"):
        print(f"QUARTER doit ressembler à 2026Q2, reçu : {QUARTER!r}")
        print("C'est le repère FISCAL de la société, celui qu'affiche le rapport.")
        return 1

    path = Path(SOURCE_FILE)
    if not path.is_file():
        print(f"Fichier introuvable : {path}")
        return 1

    text = path.read_text(errors="replace").strip()
    words = len(text.split())
    if words < MIN_WORDS:
        print(f"{words} mots seulement : ce n'est pas un earnings call entier.")
        print(f"Un vrai call en fait 6 000 à 12 000. Seuil : {MIN_WORDS}.")
        return 1

    call_date = None
    if CALL_DATE:
        try:
            call_date = date.fromisoformat(CALL_DATE)
        except ValueError:
            print(f"CALL_DATE doit être au format AAAA-MM-JJ, reçu : {CALL_DATE!r}")
            return 1

    prepared, qa = split_prepared_from_qa(text)
    transcript = CallTranscript(
        ticker=TICKER,
        call_date=call_date,
        fiscal_period=QUARTER,
        full_text=text,
        prepared_remarks=prepared,
        qa=qa,
        source=SOURCE or "transcription locale",
        # Default False, and it takes an explicit VERBATIM=1 to say
        # otherwise. Someone importing a file by hand is transcribing
        # audio far more often than they are pasting an official record,
        # and the expensive mistake is the one that makes a machine's
        # guess look like the company's own words.
        verbatim=VERBATIM,
    )

    payload = asdict(transcript)
    if call_date is not None:
        payload["call_date"] = call_date.isoformat()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{TICKER}_{QUARTER}.json"
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False))

    print(f"Écrit : {out.relative_to(ROOT)}")
    print(f"  {words} mots, {'Q&A isolée' if qa else 'Q&A non isolée'}")
    print(f"  source : {transcript.source}")
    if not transcript.verbatim:
        print("  marqué comme transcription automatique : le rapport dira que")
        print("  les citations peuvent contenir des erreurs de transcription.")
    print()
    print("Committe ce fichier, puis lance le workflow sur ce ticker :")
    print(f"  le cache est lu avant tout appel, donc {TICKER} {QUARTER} ne")
    print("  consommera aucun quota fournisseur.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
