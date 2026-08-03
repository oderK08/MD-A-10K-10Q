import json
from datetime import date
from pathlib import Path

import pytest

from equity_analyzer.data_layer.cik_lookup import (
    CikLookup,
    FilingNotFoundError,
    list_filings,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class FakeEdgarClient:
    """
    Stands in for EdgarClient in tests: returns fixture data instead of
    making real HTTP calls, and counts calls so we can assert caching
    behavior.
    """

    def __init__(self, ticker_map: dict, submissions: dict):
        self._ticker_map = ticker_map
        self._submissions = submissions
        self.ticker_map_calls = 0
        self.submissions_calls = 0

    def fetch_ticker_to_cik_map(self):
        self.ticker_map_calls += 1
        return self._ticker_map

    def fetch_submissions(self, cik: str):
        self.submissions_calls += 1
        return self._submissions


@pytest.fixture
def submissions_fixture():
    return json.loads((FIXTURES / "sample_submissions.json").read_text())


@pytest.fixture
def ticker_map():
    return {"TESTCO": "0000320193"}


def test_cik_lookup_resolves_known_ticker(ticker_map, submissions_fixture):
    client = FakeEdgarClient(ticker_map, submissions_fixture)
    lookup = CikLookup(client)
    assert lookup.resolve("testco") == "0000320193"  # case-insensitive


def test_cik_lookup_caches_ticker_map(ticker_map, submissions_fixture):
    client = FakeEdgarClient(ticker_map, submissions_fixture)
    lookup = CikLookup(client)
    lookup.resolve("TESTCO")
    lookup.resolve("TESTCO")
    assert client.ticker_map_calls == 1  # fetched once, reused after


def test_cik_lookup_unknown_ticker_raises(ticker_map, submissions_fixture):
    client = FakeEdgarClient(ticker_map, submissions_fixture)
    lookup = CikLookup(client)
    with pytest.raises(FilingNotFoundError, match="not found"):
        lookup.resolve("NOTATICKER")


def test_list_filings_filters_by_form_type(submissions_fixture):
    client = FakeEdgarClient({}, submissions_fixture)
    results = list_filings(client, "0000320193", "10-Q")
    # 3 form entries are "10-Q" or "10-Q/A" in fixture, only exact "10-Q"
    # should match (not the amendment) -- form_type filtering must be exact.
    assert len(results) == 2
    assert all(r.form_type == "10-Q" for r in results)


def test_list_filings_returns_most_recent_first(submissions_fixture):
    client = FakeEdgarClient({}, submissions_fixture)
    results = list_filings(client, "0000320193", "10-Q")
    assert results[0].accession_number == "0000320193-25-000010"
    assert results[0].filed_date == date(2025, 4, 24)
    assert results[1].accession_number == "0000320193-24-000010"


def test_list_filings_respects_limit(submissions_fixture):
    client = FakeEdgarClient({}, submissions_fixture)
    results = list_filings(client, "0000320193", "10-Q", limit=1)
    assert len(results) == 1


def test_list_filings_10k_type(submissions_fixture):
    client = FakeEdgarClient({}, submissions_fixture)
    results = list_filings(client, "0000320193", "10-K")
    assert len(results) == 2
    assert results[0].accession_number == "0000320193-24-000060"


def test_list_filings_raises_when_form_type_absent(submissions_fixture):
    client = FakeEdgarClient({}, submissions_fixture)
    with pytest.raises(FilingNotFoundError, match="No 20-F filings"):
        list_filings(client, "0000320193", "20-F")
