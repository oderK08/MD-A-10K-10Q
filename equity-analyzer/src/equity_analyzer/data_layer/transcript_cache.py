"""
A transcript, once fetched, never changes.

That single fact is what makes a 25-requests-a-day free tier workable
for a hundred-company universe, and it is worth stating plainly because
the arithmetic is otherwise discouraging:

  WITHOUT A CACHE. The report compares two quarters, so analysing one
  company costs 2 requests. A hundred companies is 200 requests, or
  eight days of free-tier budget, and it costs the same 200 again the
  next time you run it. Nothing accumulates.

  WITH A CACHE. The first sweep costs the same 200 requests, spread
  over a few days. After that, each company produces exactly ONE new
  transcript per quarter, so the standing cost is 100 requests per
  QUARTER against a budget of 25 per DAY. The free tier stops being the
  binding constraint and stays that way.

The same reasoning holds for a paid API, where the cache is money rather
than quota, and for a self-transcribed pipeline, where re-running
Whisper over an hour of audio already transcribed is pure waste.

WHAT IS AND IS NOT CACHED. Only a successful fetch. A failure is never
written, because the reasons a fetch fails are all temporary or fixable
(quota exhausted, network, a vendor outage, a wrong field name) and
caching one would turn a passing problem into a permanent hole that
looks exactly like a company with no transcript.

The store is plain JSON files on disk, one per call. Not a database:
this has to survive being committed to a repository, inspected by a
human who wants to check what the model actually read, and copied
between a laptop and a CI runner.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Optional

from .transcript_source import CallTranscript, TranscriptSource, TranscriptUnavailable

# Filenames end up in a repository and in artifact listings, so they are
# built from a restricted alphabet rather than trusting a ticker or a
# quarter label to be filesystem-safe.
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(value: str) -> str:
    return _SAFE_RE.sub("-", (value or "").strip()) or "unknown"


class TranscriptCache:
    """Reads and writes CallTranscript objects as JSON, one file per call."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def path_for(self, ticker: str, quarter: str) -> Path:
        return self.directory / f"{_slug(ticker).upper()}_{_slug(quarter)}.json"

    def get(self, ticker: str, quarter: str) -> Optional[CallTranscript]:
        path = self.path_for(ticker, quarter)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except (ValueError, OSError):
            # A truncated or hand-edited file is a cache miss, not a
            # crash: the fetch below will simply replace it.
            return None
        call_date = payload.get("call_date")
        return CallTranscript(
            ticker=payload.get("ticker", ticker),
            call_date=date.fromisoformat(call_date) if call_date else None,
            fiscal_period=payload.get("fiscal_period"),
            full_text=payload.get("full_text", ""),
            prepared_remarks=payload.get("prepared_remarks", ""),
            qa=payload.get("qa"),
            source=payload.get("source", "cache"),
        )

    def put(self, quarter: str, transcript: CallTranscript) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = asdict(transcript)
        if transcript.call_date is not None:
            payload["call_date"] = transcript.call_date.isoformat()
        path = self.path_for(transcript.ticker, quarter)
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
        return path


class CachedTranscriptSource(TranscriptSource):
    """
    Any `TranscriptSource`, with a disk cache in front of it.

    A decorator rather than a feature of each source, so that caching
    behaves identically whether the transcript came free from an EDGAR
    exhibit, from a metered API, or from transcribing the audio -- and
    so that adding a new source cannot accidentally ship without it.
    """

    def __init__(self, inner: TranscriptSource, cache: TranscriptCache):
        self._inner = inner
        self._cache = cache
        self.hits = 0
        self.fetches = 0

    @property
    def name(self) -> str:
        return f"{self._inner.name} (avec cache)"

    def fetch(self, ticker: str, cik: str, client=None, quarter: Optional[str] = None) -> CallTranscript:
        key = quarter or "latest"
        if quarter:
            # "latest" is deliberately never served FROM the cache: the
            # newest quarter is exactly the one that changes, and
            # returning a stale "latest" would silently analyse the
            # previous quarter as if it were this one -- the same class
            # of error this whole redesign exists to remove.
            cached = self._cache.get(ticker, key)
            if cached is not None:
                self.hits += 1
                return cached

        # Counted BEFORE the call, not after. A request that comes back
        # empty or refused still spent one of the day's twenty five, and
        # a counter that only tallies successes tells the reader the run
        # was cheaper than it was. On the first real MSFT run it
        # reported one API call where two had gone out.
        self.fetches += 1
        transcript = self._call_inner(ticker, cik, client, quarter)
        # Written only on success: a cached failure would be
        # indistinguishable from a company that has no transcript, and
        # every reason a fetch fails is temporary or fixable.
        self._cache.put(transcript.fiscal_period or key, transcript)
        return transcript

    def _call_inner(self, ticker, cik, client, quarter):
        try:
            return self._inner.fetch(ticker, cik, client, quarter=quarter)
        except TypeError:
            # Sources that predate the `quarter` argument (the EDGAR
            # exhibit one has no use for it) keep the shorter signature.
            return self._inner.fetch(ticker, cik, client)


__all__ = ["CachedTranscriptSource", "TranscriptCache"]
