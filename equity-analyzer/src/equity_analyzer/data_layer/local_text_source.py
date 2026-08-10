"""
A transcript you dropped in the repository, as a plain text file.

WHY PLAIN TEXT AND NOT THE CACHE'S JSON. The cache already accepts a
hand made entry, but only in its own JSON shape, which in practice means
running a script to build it. That assumes a terminal. Someone holding
only a browser can still create a file on github.com, paste in what
Whisper produced, and commit it, and that should be enough: the point of
this route is to work when the provider does not have the call, and a
missing terminal is not a good reason to lose the quarter.

SO THE NAME CARRIES THE METADATA the JSON would have carried:

    transcripts/UBER_2026Q2.txt

The quarter is the ISSUER's fiscal label, the same one the report prints
and the same one the provider indexes by. Naming the file after it does
two jobs at once. It says which call this is, and it makes the file
EXPIRE ON ITS OWN: next quarter the pipeline resolves 2026Q3, no file
matches, and the provider is used again. A file named `UBER.txt` would
have quietly overridden every future run with a stale call, which is the
kind of failure nobody goes looking for.

NOT TREATED AS VERBATIM. Whatever arrives this way is assumed to be a
machine transcription, because that is overwhelmingly what it is, and
the expensive mistake is the one that lets a machine's guess wear the
authority of the company's own written record (see
`CallTranscript.verbatim`). The report says so above the reading.

THE PAIRING IS STILL CHECKED. The label in the filename is a claim by
whoever named it, and a wrong one would attach a real reading to the
wrong three months. `transcript_period.verify_against_declared` compares
it against the period the company states out loud in the opening of the
call itself, so a mismatch surfaces on the report instead of passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .transcript_source import (
    CallTranscript,
    TranscriptSource,
    TranscriptUnavailable,
    split_prepared_from_qa,
)

# Same floor as the import script, for the same reason: a real earnings
# call runs six to twelve thousand words, so a few hundred means a
# truncated paste, a summary, or the wrong document. Refusing here beats
# refusing after the model has read it, and beats not refusing at all.
MIN_WORDS = 1500


def expected_filename(ticker: str, quarter: str) -> str:
    """The file to create for this call. Printed in logs and errors."""
    return f"{ticker.upper()}_{quarter}.txt"


@dataclass
class LocalTextSource(TranscriptSource):
    """A `transcripts/TICKER_QUARTER.txt` committed to the repository."""

    directory: Path
    name: str = "transcript déposé dans le dépôt"

    def fetch(self, ticker: str, cik: str, client=None, quarter: Optional[str] = None) -> CallTranscript:
        if not quarter:
            raise TranscriptUnavailable(
                "pas de repère de trimestre : impossible de savoir quel fichier lire"
            )
        path = Path(self.directory) / expected_filename(ticker, quarter)
        if not path.is_file():
            raise TranscriptUnavailable(f"pas de fichier {path.name}")

        text = path.read_text(errors="replace").strip()
        words = len(text.split())
        if words < MIN_WORDS:
            # Raised rather than returned, and loudly: a half pasted file
            # would otherwise produce a confident reading of a fragment.
            raise TranscriptUnavailable(
                f"{path.name} ne fait que {words} mots. Un earnings call en fait "
                f"6 000 à 12 000, donc ce fichier est tronqué, résumé, ou n'est pas "
                f"le bon document. Seuil : {MIN_WORDS}."
            )

        prepared, qa = split_prepared_from_qa(text)
        return CallTranscript(
            ticker=ticker.upper(),
            call_date=None,
            fiscal_period=quarter,
            full_text=text,
            prepared_remarks=prepared,
            qa=qa,
            source=f"{self.name} ({path.name})",
            verbatim=False,
        )


__all__ = ["LocalTextSource", "MIN_WORDS", "expected_filename"]
