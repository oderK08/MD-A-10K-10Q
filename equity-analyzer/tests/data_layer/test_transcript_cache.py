"""
Tests for the transcript cache.

A transcript, once fetched, never changes -- which is what turns a
25-requests-a-day free tier from the binding constraint into a
non-issue. Without a cache, a hundred companies costs 200 requests every
single run and nothing accumulates. With one, the standing cost is a
hundred requests per QUARTER against a budget of 25 per DAY.
"""

import json

import pytest

from equity_analyzer.data_layer.transcript_cache import CachedTranscriptSource, TranscriptCache
from equity_analyzer.data_layer.transcript_source import (
    CallTranscript,
    TranscriptSource,
    TranscriptUnavailable,
)


def _transcript(ticker="AAOI", period="2026Q3", text="Prepared remarks about the quarter."):
    return CallTranscript(
        ticker=ticker, call_date=None, fiscal_period=period,
        full_text=text, prepared_remarks=text, qa=None, source="test source",
    )


class _CountingSource(TranscriptSource):
    name = "counting"

    def __init__(self, result=None, error=None):
        self.calls = 0
        self._result = result
        self._error = error

    def fetch(self, ticker, cik, client=None, quarter=None):
        self.calls += 1
        if self._error:
            raise self._error
        return self._result or _transcript(ticker, quarter or "2026Q3")


def test_a_second_request_for_the_same_quarter_costs_nothing(tmp_path):
    inner = _CountingSource()
    source = CachedTranscriptSource(inner, TranscriptCache(tmp_path))

    first = source.fetch("AAOI", "0001158114", quarter="2026Q3")
    second = source.fetch("AAOI", "0001158114", quarter="2026Q3")

    assert inner.calls == 1
    assert source.hits == 1
    assert second.full_text == first.full_text


def test_a_round_trip_through_disk_preserves_the_split_call(tmp_path):
    cache = TranscriptCache(tmp_path)
    original = CallTranscript(
        ticker="AAOI", call_date=None, fiscal_period="2026Q3",
        full_text="Prepared.\nQ&A begins.", prepared_remarks="Prepared.",
        qa="Q&A begins.", source="Alpha Vantage",
    )
    cache.put("2026Q3", original)
    restored = cache.get("AAOI", "2026Q3")

    assert restored.prepared_remarks == "Prepared."
    assert restored.qa == "Q&A begins."
    assert restored.source == "Alpha Vantage"


def test_the_newest_quarter_is_never_served_from_cache(tmp_path):
    """
    "latest" is exactly the quarter that changes. Serving a stale one
    would silently analyse the previous quarter as if it were this one,
    which is the error this whole redesign exists to remove.
    """
    inner = _CountingSource()
    source = CachedTranscriptSource(inner, TranscriptCache(tmp_path))

    source.fetch("AAOI", "0001158114")
    source.fetch("AAOI", "0001158114")

    assert inner.calls == 2
    assert source.hits == 0


def test_a_failed_fetch_is_never_cached(tmp_path):
    """
    Every reason a fetch fails -- quota, network, a vendor outage, a
    wrong field name -- is temporary or fixable. Caching one would turn
    a passing problem into a permanent hole that looks exactly like a
    company with no transcript.
    """
    cache = TranscriptCache(tmp_path)
    inner = _CountingSource(error=TranscriptUnavailable("quota exhausted"))
    source = CachedTranscriptSource(inner, cache)

    with pytest.raises(TranscriptUnavailable):
        source.fetch("AAOI", "0001158114", quarter="2026Q3")

    assert cache.get("AAOI", "2026Q3") is None
    assert list(tmp_path.glob("*.json")) == []


def test_a_corrupt_cache_file_is_a_miss_not_a_crash(tmp_path):
    cache = TranscriptCache(tmp_path)
    cache.path_for("AAOI", "2026Q3").write_text("{ truncated")
    assert cache.get("AAOI", "2026Q3") is None

    source = CachedTranscriptSource(_CountingSource(), cache)
    assert source.fetch("AAOI", "0001158114", quarter="2026Q3") is not None


def test_filenames_stay_safe_and_inspectable(tmp_path):
    """
    These files get committed to a repository and read by a human who
    wants to check what the model actually saw, so the name has to be
    both safe and obvious.
    """
    cache = TranscriptCache(tmp_path)
    cache.put("2026 Q3", _transcript(ticker="brk.b", period="2026 Q3"))
    written = list(tmp_path.glob("*.json"))
    assert len(written) == 1
    assert written[0].name == "BRK.B_2026-Q3.json"
    # and it is readable JSON, not a pickle or a blob
    assert json.loads(written[0].read_text())["ticker"] == "brk.b"


def test_sources_that_do_not_take_a_quarter_still_work(tmp_path):
    """
    The EDGAR exhibit source has no use for a quarter argument. The
    cache must not force every source to grow one.
    """
    class _NoQuarter(TranscriptSource):
        name = "edgar-like"

        def fetch(self, ticker, cik, client=None):
            return _transcript(ticker)

    source = CachedTranscriptSource(_NoQuarter(), TranscriptCache(tmp_path))
    assert source.fetch("AAOI", "0001158114", quarter="2026Q3") is not None
