"""
What earlier runs learned, kept instead of thrown away.

WHY. Every run so far produced a sheet of the previous quarter's
quantified commitments, used it once, and discarded it. The cache went
out as a CI artifact, which expires. So the tool could say what changed
since last quarter and nothing more, while the question worth asking
about a management team is the one only a SERIES answers: it promised
this a year ago, did it deliver.

The same applies to the Q&A. One dodged question is noise. The same
figure refused to the same kind of question four quarters running is a
finding, and it cannot be seen from inside a single call.

WHAT IS STORED, and it is deliberately only what a run already
computed:

  THE COMMITMENTS OF A CALL, under that call's own quarter. Note the
  asymmetry: a run reading Q4 extracts the commitments of Q3, so the
  store fills in one quarter behind, at no extra cost. Nothing here
  triggers work that the report did not already need.

  THE DODGES OF A CALL, under the quarter read, straight off the Q&A
  pass.

IT ALSO MAKES RUNS CHEAPER, which was not the point but is the reason
the read path comes first. A sheet already on disk is a provider request
and a model call that do not happen. On a ticker followed for a year,
most of the baseline is already known.

WHY PLAIN JSON, ONE FILE PER TICKER AND QUARTER. It survives being
committed, it is readable on github.com without tooling, two runs on
different tickers cannot collide, and a bad record can be deleted by
hand. A database would buy nothing here and would not survive a runner
that is destroyed after every job.

WHAT THIS IS NOT. It is not a cache of transcripts (that already exists
next door, and holds far more text), and it never stores prose written
by a model about a quarter. Only the structured findings, so that a
change in how the report is worded never invalidates the history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# A record is written under `<root>/<TICKER>/<QUARTER>.json`. The ticker
# directory keeps a portfolio's worth of history browsable, and the
# quarter filename means a record expires by itself the way transcripts
# do: nothing ever overwrites another quarter by accident.
_SUFFIX = ".json"


def _quarter_sort_key(quarter: str):
    """
    Chronological order for labels like "2026Q3".

    Sorted numerically rather than as strings so that a year rollover
    orders correctly, and defensively so that a hand written filename
    cannot raise in the middle of a run.
    """
    try:
        year, _, index = quarter.partition("Q")
        return (int(year), int(index))
    except (TypeError, ValueError):
        return (0, 0)


@dataclass(frozen=True)
class QuarterRecord:
    """What one run learned about one quarter."""

    ticker: str
    quarter: str
    commitments: list
    dodges: list
    # Which model produced the extraction, kept so a future change of
    # model is visible in the series rather than silently mixed in.
    model: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not (self.commitments or self.dodges)

    def merged_with(self, other: "QuarterRecord") -> "QuarterRecord":
        """
        This record, filled in from `other` where it has nothing.

        A quarter is written twice for a good reason: the run that READS
        it knows its dodges, and the run one quarter later knows its
        commitments, because a run reading Q4 extracts Q3's baseline.
        Neither may erase what the other learned, and since they fill
        different fields, gap filling is all it takes.

        WHERE BOTH HAVE SOMETHING, THE NEW ONE WINS. That case only
        arises on a re-run of the same quarter, and there the fresh
        extraction is the one to keep: it makes re-running the way to
        repair a bad record, instead of requiring someone to find and
        delete a file first. Nothing downstream depends on a record
        never changing, so immutability would buy nothing and cost that.
        """
        return QuarterRecord(
            ticker=self.ticker or other.ticker,
            quarter=self.quarter or other.quarter,
            commitments=self.commitments or other.commitments,
            dodges=self.dodges or other.dodges,
            model=self.model or other.model,
        )


@dataclass
class HistoryStore:
    """The records on disk, under one root directory."""

    root: Path

    def _path(self, ticker: str, quarter: str) -> Path:
        return Path(self.root) / ticker.upper() / f"{quarter}{_SUFFIX}"

    def get(self, ticker: str, quarter: str) -> Optional[QuarterRecord]:
        """
        One record, or None.

        Never raises on a damaged file. A record is an optimisation and
        an extra, so an unreadable one has to degrade to "not known
        yet": failing a report because a JSON file was truncated by an
        interrupted run would be the tail wagging the dog.
        """
        path = self._path(ticker, quarter)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text())
        except (ValueError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        return QuarterRecord(
            ticker=str(payload.get("ticker", ticker)).upper(),
            quarter=str(payload.get("quarter", quarter)),
            commitments=payload.get("commitments") or [],
            dodges=payload.get("dodges") or [],
            model=payload.get("model"),
        )

    def put(self, record: QuarterRecord) -> Optional[Path]:
        """
        Writes a record, merged with whatever is already there. Returns
        the path written, or None when there was nothing worth keeping.

        Never raises, for the reason `get` does not: a read only
        filesystem or a full disk must cost the history, not the report.
        """
        if record.is_empty:
            return None
        existing = self.get(record.ticker, record.quarter)
        if existing is not None:
            record = record.merged_with(existing)
        path = self._path(record.ticker, record.quarter)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "ticker": record.ticker,
                "quarter": record.quarter,
                "model": record.model,
                "commitments": record.commitments,
                "dodges": record.dodges,
            }, indent=1, ensure_ascii=False))
        except OSError:
            return None
        return path

    def history_before(self, ticker: str, quarter: str, limit: int = 4) -> list:
        """
        The records for quarters STRICTLY BEFORE `quarter`, newest
        first, at most `limit`.

        Strictly before, because the quarter being read is the thing
        being judged: including it would let the report check the
        company's promises against the call that made them.
        """
        directory = Path(self.root) / ticker.upper()
        if not directory.is_dir():
            return []
        cutoff = _quarter_sort_key(quarter)
        labels = sorted(
            (p.stem for p in directory.glob(f"*{_SUFFIX}")),
            key=_quarter_sort_key,
            reverse=True,
        )
        records = []
        for label in labels:
            if _quarter_sort_key(label) >= cutoff:
                continue
            record = self.get(ticker, label)
            if record is not None and not record.is_empty:
                records.append(record)
            if len(records) >= limit:
                break
        return records


__all__ = ["HistoryStore", "QuarterRecord"]
