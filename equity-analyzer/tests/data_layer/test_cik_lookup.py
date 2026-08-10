import json
from datetime import date
from pathlib import Path

import pytest

from equity_analyzer.data_layer.cik_lookup import (
    CikLookup,
    FilingNotFoundError,
    latest_reported_period,
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


def test_list_filings_accepts_several_form_types(submissions_fixture):
    """
    A quarterly sequence crosses the annual boundary -- the filing before
    a Q1 is the 10-K -- so listing has to be able to return both forms in
    one recency-ordered list, not two separate lists to be merged by the
    caller.
    """
    client = FakeEdgarClient({}, submissions_fixture)
    results = list_filings(client, "0000320193", ["10-Q", "10-K"])
    assert [r.form_type for r in results] == ["10-Q", "10-K", "10-Q", "10-K"]
    # the "10-Q/A" amendment is still excluded: form matching stays exact
    assert all(r.form_type in ("10-Q", "10-K") for r in results)


def _submissions(rows):
    """rows: list of (form, accession, filing_date, report_date)."""
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "filingDate": [r[2] for r in rows],
                "reportDate": [r[3] for r in rows],
                "primaryDocument": [f"{r[1]}.htm" for r in rows],
            }
        }
    }


def test_latest_reported_period_takes_the_newest_quarter():
    client = FakeEdgarClient({}, _submissions([
        ("10-Q", "acc-q3", "2026-09-04", "2026-08-28"),
        ("10-Q", "acc-q2", "2026-06-05", "2026-05-29"),
        ("10-K", "acc-fy",  "2025-10-10", "2025-08-29"),
    ]))
    assert latest_reported_period(client, "0000320193").accession_number == "acc-q3"


def test_the_fourth_quarter_is_found_even_though_it_is_a_10k():
    """
    THE bug this function exists for, found on a real MSFT run. A fiscal
    year has four quarters but only three 10-Qs: the fourth is reported
    inside the 10-K. Microsoft's year ends in June, so its Q4 ends 30
    June and is filed in late July. Selecting the newest 10-Q in August
    returned the quarter ended 31 March, and the tool read an April
    earnings call while presenting it as the latest one.

    Silent, too: a Q3 filing is a perfectly valid filing, and nothing
    downstream can tell it is the wrong one.
    """
    client = FakeEdgarClient({}, _submissions([
        ("10-K", "acc-fy2026", "2026-07-30", "2026-06-30"),
        ("10-Q", "acc-q3", "2026-04-24", "2026-03-31"),
        ("10-Q", "acc-q2", "2026-01-28", "2025-12-31"),
    ]))
    assert latest_reported_period(client, "0000320193").accession_number == "acc-fy2026"


def test_latest_reported_period_orders_by_period_not_filing_date():
    """
    A filing submitted late still belongs to its own period. Ordering by
    filed_date would make a delinquent Q2 filed after Q3 look like the
    newest quarter.
    """
    client = FakeEdgarClient({}, _submissions([
        ("10-Q", "acc-q2-late", "2026-09-30", "2026-05-29"),
        ("10-Q", "acc-q3", "2026-09-04", "2026-08-28"),
    ]))
    assert latest_reported_period(client, "0000320193").accession_number == "acc-q3"


def test_a_filer_with_only_annual_reports_still_resolves():
    """
    Not every issuer files 10-Qs. Refusing them would be refusing a
    company that has an annual report and an annual earnings call.
    """
    client = FakeEdgarClient({}, _submissions([
        ("10-K", "acc-fy", "2025-10-10", "2025-08-29"),
        ("10-K", "acc-fy-1", "2024-10-10", "2024-08-30"),
    ]))
    assert latest_reported_period(client, "0000320193").accession_number == "acc-fy"


def test_latest_reported_period_raises_when_there_is_nothing_periodic():
    client = FakeEdgarClient({}, _submissions([
        ("8-K", "acc-8k", "2026-07-30", "2026-06-30"),
    ]))
    with pytest.raises(FilingNotFoundError):
        latest_reported_period(client, "0000320193")
