"""
Tests for the transcript-coverage survey.

The survey's job is to replace a judgement call ("should we build our own
transcript pipeline?") with a table. What has to hold for that table to
be worth anything: it must never confuse "this company offers nothing"
with "this run failed to ask", and one unreachable IR site must not take
down a hundred-ticker run.
"""

import pytest

from equity_analyzer.data_layer.webcast_discovery import (
    detect_vendors,
    find_audio_links,
    investor_relations_url,
    survey_ticker,
)

CIK = "0000320193"


class _EdgarClient:
    def __init__(self, submissions, index=None, raise_on_index=False):
        self._submissions = submissions
        self._index = index or {"directory": {"item": []}}
        self._raise_on_index = raise_on_index

    def fetch_submissions(self, cik):
        return self._submissions

    def fetch_filing_index(self, cik, accession_number):
        if self._raise_on_index:
            raise RuntimeError("EDGAR is down")
        return self._index


def _submissions(*, ir_url=None, with_8k=True):
    payload = {
        "filings": {"recent": {
            "form": ["8-K"] if with_8k else ["10-Q"],
            "items": ["2.02"] if with_8k else [""],
            "accessionNumber": ["acc-e"],
            "filingDate": ["2026-08-06"],
            "reportDate": ["2026-08-06"],
            "primaryDocument": ["form8k.htm"],
        }}
    }
    if ir_url:
        payload["investorWebsite"] = ir_url
    return payload


class _Fetcher:
    def __init__(self, html=None, error=None):
        self._html = html
        self._error = error
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        if self._error:
            raise self._error
        return self._html


# --- vendor detection ----------------------------------------------------


def test_detects_the_common_webcast_hosts():
    assert [s.vendor for s in detect_vendors('<script src="https://s2.q4cdn.com/x.js">')] == ["Q4 Inc"]
    assert [s.vendor for s in detect_vendors('<a href="https://www.notified.com/e/123">')] == [
        "Notified / Intrado"
    ]


def test_a_page_can_carry_several_hosts_and_the_most_specific_leads():
    """
    A Q4-built IR site embedding a YouTube replay is both. Order matters:
    the primary host is the one whose adapter would be written.
    """
    html = '<script src="https://s2.q4cdn.com/a.js"></script><iframe src="https://youtube.com/embed/x">'
    assert [s.vendor for s in detect_vendors(html)] == ["Q4 Inc", "YouTube"]


def test_each_detected_vendor_carries_what_fetching_it_would_take():
    """
    "In what format" is half the question being asked. A survey that
    named the vendor without saying what fetching it involves would
    answer only the easy half.
    """
    signatures = detect_vendors('<a href="https://feeds.megaphone.fm/earnings">')
    assert signatures[0].vendor == "podcast feed"
    assert "RSS enclosure" in signatures[0].fetch_route


def test_direct_media_links_are_found_and_absolutised():
    """
    A company linking straight to an MP3 needs no adapter at all, which
    is worth spotting separately from any vendor match.
    """
    html = '<a href="/media/q3-2026-call.mp3">Replay</a><a href="https://cdn.x.com/b.m4a?t=1">B</a>'
    links = find_audio_links(html, base_url="https://ir.example.com/events")
    assert links[0] == "https://ir.example.com/media/q3-2026-call.mp3"
    assert links[1] == "https://cdn.x.com/b.m4a"


def test_a_page_with_no_media_and_no_vendor_returns_nothing_rather_than_a_guess():
    assert detect_vendors("<p>Our next earnings call will be announced.</p>") == ()
    assert find_audio_links("<p>Nothing here.</p>") == ()


# --- the IR address ------------------------------------------------------


def test_the_ir_url_comes_from_sec_rather_than_being_constructed():
    """
    The issuer told the SEC where its IR site is. Guessing at
    "ir.<company>.com" would be a construction, not a source.
    """
    assert investor_relations_url({"investorWebsite": "https://ir.apple.com"}) == "https://ir.apple.com"
    # a bare host is still usable
    assert investor_relations_url({"website": "investors.example.com"}) == "https://investors.example.com"
    assert investor_relations_url({}) is None


# --- the survey itself ---------------------------------------------------


def test_an_edgar_transcript_exhibit_short_circuits_everything_else():
    client = _EdgarClient(
        _submissions(ir_url="https://ir.example.com"),
        {"directory": {"item": [
            {"name": "ex992.htm", "type": "EX-99.2", "description": "Earnings Call Transcript"},
        ]}},
    )
    coverage = survey_ticker("TEST", CIK, edgar_client=client, page_fetcher=_Fetcher("<html></html>"))
    assert coverage.edgar_transcript_exhibit == "ex992.htm"
    assert coverage.verdict == "EDGAR"


def test_a_registration_wall_is_reported_as_such_not_as_a_usable_vendor():
    """
    The outcome that decides whether the DIY route is viable at all. It
    must not be filed under the same heading as a fetchable vendor.
    """
    client = _EdgarClient(_submissions(ir_url="https://ir.example.com"))
    html = '<script src="https://notified.com/p.js"></script><p>Registration required to listen.</p>'
    coverage = survey_ticker("TEST", CIK, edgar_client=client, page_fetcher=_Fetcher(html))
    assert coverage.vendors == ("Notified / Intrado",)
    assert coverage.registration_gated is True
    assert coverage.verdict == "gated"


def test_an_unreachable_ir_site_is_recorded_not_reported_as_offering_nothing():
    """
    "We could not reach it" and "it offers nothing" must never collapse
    into the same cell: one is a company to skip, the other is a run to
    repeat.
    """
    client = _EdgarClient(_submissions(ir_url="https://ir.example.com"))
    coverage = survey_ticker(
        "TEST", CIK, edgar_client=client, page_fetcher=_Fetcher(error=TimeoutError("timed out")),
    )
    assert coverage.verdict == "erreur"
    assert "IR page" in coverage.error
    assert coverage.vendors == ()


def test_one_broken_company_does_not_raise_and_take_down_the_survey():
    client = _EdgarClient(_submissions(with_8k=False), raise_on_index=True)
    coverage = survey_ticker("TEST", CIK, edgar_client=client, page_fetcher=None)
    assert coverage.error is not None
    assert coverage.edgar_transcript_exhibit is None


def test_a_company_with_a_plain_mp3_is_the_best_non_edgar_outcome():
    client = _EdgarClient(_submissions(ir_url="https://ir.example.com"))
    html = '<a href="https://cdn.example.com/q3.mp3">Replay</a>'
    coverage = survey_ticker("TEST", CIK, edgar_client=client, page_fetcher=_Fetcher(html))
    assert coverage.verdict == "audio direct"
    assert coverage.direct_audio_links == ("https://cdn.example.com/q3.mp3",)


# --- the distinction the first real run got wrong ------------------------


def test_a_company_with_no_ir_address_is_not_reported_as_offering_nothing():
    """
    The first real run of this survey returned "rien trouvé" for a
    company whose IR page was never even fetched, because SEC carried no
    address for it. That reads as a measurement and is not one. The two
    must have different verdicts.
    """
    client = _EdgarClient(_submissions(ir_url=None))
    coverage = survey_ticker("MSFT", CIK, edgar_client=client, page_fetcher=_Fetcher("<html/>"))

    assert coverage.verdict == "IR non lue"
    assert coverage.ir_page_read is False
    assert "pas d'URL IR" in coverage.ir_note


def test_the_missing_address_note_names_the_fields_sec_did_return():
    """
    Same discipline as the Alpha Vantage check: when a guess about
    someone else's payload is wrong, the output should say what the
    right answer is rather than leaving a silent hole.
    """
    client = _EdgarClient(_submissions(ir_url=None))
    client._submissions["name"] = "MICROSOFT CORP"
    client._submissions["sicDescription"] = "Services-Prepackaged Software"

    coverage = survey_ticker("MSFT", CIK, edgar_client=client, page_fetcher=_Fetcher("<html/>"))
    assert "name" in coverage.ir_note
    assert "sicDescription" in coverage.ir_note


def test_a_page_actually_read_and_matching_nothing_is_a_real_finding():
    client = _EdgarClient(_submissions(ir_url="https://ir.example.com"))
    coverage = survey_ticker(
        "TEST", CIK, edgar_client=client,
        page_fetcher=_Fetcher("<p>Our next earnings call will be announced.</p>"),
    )
    assert coverage.ir_page_read is True
    assert coverage.verdict == "rien trouvé"
    assert coverage.ir_note is None
