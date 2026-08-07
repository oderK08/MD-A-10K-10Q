"""
Tests for the transcript seam.

The pipeline is written against `TranscriptSource` so that where a
transcript comes from is one class and one line of wiring, not a change
threaded through the diff, the selection and the summary.
"""

import pytest

from equity_analyzer.data_layer.transcript_source import (
    EdgarExhibitSource,
    HttpTranscriptSource,
    TranscriptUnavailable,
    split_prepared_from_qa,
)

CIK = "0001158114"


# --- prepared remarks vs Q&A --------------------------------------------


def test_the_call_is_split_at_the_operators_handover():
    """
    The two halves behave differently under a diff and must not be
    treated as one body of text. Prepared remarks are scripted and
    compare cleanly against last quarter's; Q&A is unscripted, and its
    value is in which topics analysts pushed on.
    """
    text = (
        "Operator\n"
        "Good afternoon and welcome to the third quarter earnings call.\n"
        "Chief Executive Officer\n"
        "Revenue grew 12% and we expect prices to rise next quarter.\n"
        "Operator\n"
        "We will now begin the question-and-answer session.\n"
        "Analyst\n"
        "Can you walk through the gross margin bridge?\n"
    )
    prepared, qa = split_prepared_from_qa(text)
    assert "prices to rise next quarter" in prepared
    assert "gross margin bridge" in qa
    assert "gross margin bridge" not in prepared


def test_only_the_first_handover_splits_the_call():
    """
    "Our next question comes from..." recurs throughout the Q&A.
    Splitting on a later occurrence would fold most of the Q&A back into
    the prepared remarks.
    """
    text = (
        "Prepared commentary about the quarter.\n"
        "We will now begin the question-and-answer session.\n"
        "First analyst question.\n"
        "Our next question comes from the line of someone else.\n"
        "Second analyst question.\n"
    )
    prepared, qa = split_prepared_from_qa(text)
    assert prepared == "Prepared commentary about the quarter."
    assert "First analyst question." in qa
    assert "Second analyst question." in qa


def test_a_call_with_no_locatable_handover_is_not_silently_halved():
    text = "A short statement with no operator handover anywhere in it."
    prepared, qa = split_prepared_from_qa(text)
    assert prepared == text
    assert qa is None


# --- EDGAR exhibit source ------------------------------------------------


class _FakeClient:
    def __init__(self, submissions, index, documents):
        self._submissions = submissions
        self._index = index
        self._documents = documents

    def fetch_submissions(self, cik):
        return self._submissions

    def fetch_filing_index(self, cik, accession_number):
        return self._index

    def fetch_filing_document(self, cik, accession_number, document):
        return self._documents[document]


def _submissions():
    return {"filings": {"recent": {
        "form": ["8-K"], "items": ["2.02"], "accessionNumber": ["acc-e"],
        "filingDate": ["2026-08-06"], "reportDate": ["2026-08-06"],
        "primaryDocument": ["form8k.htm"],
    }}}


def test_a_filer_without_a_transcript_exhibit_says_so_rather_than_substituting():
    """
    Most issuers never attach one. Handing back the press release under
    the name "transcript" would be the kind of quiet substitution that
    makes a whole report untrustworthy.
    """
    client = _FakeClient(
        _submissions(),
        {"directory": {"item": [
            {"name": "ex991.htm", "type": "EX-99.1", "description": "Press Release"},
        ]}},
        {},
    )
    with pytest.raises(TranscriptUnavailable, match="did not attach a transcript"):
        EdgarExhibitSource().fetch("AAOI", CIK, client)


def test_a_transcript_exhibit_is_read_and_split():
    client = _FakeClient(
        _submissions(),
        {"directory": {"item": [
            {"name": "ex991.htm", "type": "EX-99.1", "description": "Press Release"},
            {"name": "ex992.htm", "type": "EX-99.2", "description": "Earnings Call Transcript"},
        ]}},
        {"ex992.htm": (
            "<p>Operator</p><p>Welcome to the call.</p>"
            "<p>Chief Executive Officer</p>"
            "<p>We expect average selling prices to increase approximately 5%.</p>"
            "<p>We will now begin the question-and-answer session.</p>"
            "<p>Analyst</p><p>What drove the margin change?</p>"
        )},
    )
    transcript = EdgarExhibitSource().fetch("AAOI", CIK, client)
    assert "increase approximately 5%" in transcript.prepared_remarks
    assert "What drove the margin change?" in transcript.qa
    assert "ex992.htm" in transcript.source


# --- the licensed-API adapter -------------------------------------------


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def test_the_http_adapter_reads_whatever_field_names_it_is_configured_with(monkeypatch):
    """
    The vendors' sites are unreachable from the environment this was
    written in, so field names are configuration rather than hardcoded
    guesses: pointing them at what the vendor actually returns must not
    require a code change.
    """
    from equity_analyzer.data_layer import transcript_source

    monkeypatch.setenv("TRANSCRIPT_API_KEY", "sk-test")
    captured = {}

    def _get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Response([{
            "content": "Prepared remarks.\nWe will now begin the question-and-answer session.\nA question.",
            "eventDate": "2026-08-06",
            "period": "Q3",
        }])

    monkeypatch.setattr(transcript_source.requests, "get", _get)

    source = HttpTranscriptSource(
        url_template="https://vendor.example/transcript?symbol={ticker}",
        text_field="content", date_field="eventDate", period_field="period",
    )
    transcript = source.fetch("AAOI", CIK)

    assert captured["url"] == "https://vendor.example/transcript?symbol=AAOI"
    assert captured["headers"]["X-Api-Key"] == "sk-test"
    assert transcript.fiscal_period == "Q3"
    assert transcript.call_date.isoformat() == "2026-08-06"
    # the handover line itself belongs to the Q&A half: the split moves
    # the boundary, it never drops a line
    assert transcript.prepared_remarks == "Prepared remarks."
    assert transcript.qa.endswith("A question.")
    assert "question-and-answer session" in transcript.qa


def test_a_missing_key_is_reported_rather_than_calling_the_endpoint(monkeypatch):
    monkeypatch.delenv("TRANSCRIPT_API_KEY", raising=False)
    source = HttpTranscriptSource(url_template="https://vendor.example/{ticker}")
    with pytest.raises(TranscriptUnavailable, match="no transcript API key"):
        source.fetch("AAOI", CIK)


def test_an_unexpected_response_shape_fails_loudly(monkeypatch):
    """
    A 200 with an unexpected body is the dangerous case: it looks like
    success. It must surface as unavailable, never as an empty
    transcript, because downstream an empty transcript and a missing one
    would be indistinguishable.
    """
    from equity_analyzer.data_layer import transcript_source

    monkeypatch.setenv("TRANSCRIPT_API_KEY", "sk-test")
    monkeypatch.setattr(
        transcript_source.requests, "get",
        lambda *a, **k: _Response({"message": "quota exceeded"}),
    )
    source = HttpTranscriptSource(url_template="https://vendor.example/{ticker}")
    with pytest.raises(TranscriptUnavailable, match="no 'transcript' field"):
        source.fetch("AAOI", CIK)
