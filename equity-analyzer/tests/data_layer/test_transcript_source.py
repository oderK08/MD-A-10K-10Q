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
    with pytest.raises(TranscriptUnavailable, match="no usable 'transcript'"):
        source.fetch("AAOI", CIK)


# --- Alpha Vantage: the route worth trying before any of the hard ones ---


def test_the_alpha_vantage_preset_authenticates_by_query_string_not_header(monkeypatch):
    """
    Vendors split about evenly between a header and a query parameter.
    Alpha Vantage uses `apikey` in the query string; sending it as a
    header would come back as a polite 200 with an error payload, which
    is the worst kind of failure.
    """
    from equity_analyzer.data_layer import transcript_source
    from equity_analyzer.data_layer.transcript_source import alpha_vantage_source

    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-test")
    captured = {}

    def _get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        return _Response({"symbol": "AAOI", "quarter": "2026Q3", "transcript": [
            {"speaker": "Operator", "title": "", "content": "Welcome to the call."},
            {"speaker": "Chun Lin Hsieh", "title": "CEO",
             "content": "We expect average selling prices to increase approximately 5%."},
            {"speaker": "Operator", "title": "",
             "content": "We will now begin the question-and-answer session."},
            {"speaker": "Analyst", "title": "Cowen", "content": "What drove the margin change?"},
        ]})

    monkeypatch.setattr(transcript_source.requests, "get", _get)
    transcript = alpha_vantage_source().fetch("AAOI", "0001158114", quarter="2026Q3")

    assert captured["params"]["apikey"] == "av-test"
    assert not captured["headers"]
    assert "symbol=AAOI" in captured["url"] and "quarter=2026Q3" in captured["url"]
    assert transcript.source == "Alpha Vantage"
    # and the call is still split at the operator's handover
    assert "increase approximately 5%" in transcript.prepared_remarks
    assert "What drove the margin change?" in transcript.qa


def test_speaker_turns_arrive_attributed_to_who_said_them(monkeypatch):
    """
    A turn list is the better shape to receive, not a complication to
    flatten away. Attribution is load-bearing for the reading: "we
    expect prices to increase 5%" from the CEO in prepared remarks and
    the same sentence from an analyst asking a question are opposite
    facts, and a flattened transcript cannot tell them apart. The
    speaker goes on its own line above the content so the model reads
    the call the way a listener heard it.
    """
    from equity_analyzer.data_layer import transcript_source
    from equity_analyzer.data_layer.transcript_source import alpha_vantage_source

    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-test")
    monkeypatch.setattr(
        transcript_source.requests, "get",
        lambda *a, **k: _Response({"transcript": [
            {"speaker": "Chun Lin Hsieh", "title": "CEO",
             "content": "We expect average selling prices to increase approximately 5%."},
            {"speaker": "Stefan Murry", "title": "CFO",
             "content": "Gross margin was 32% in the quarter."},
        ]}),
    )
    transcript = alpha_vantage_source().fetch("AAOI", "0001158114", quarter="2026Q3")

    lines = transcript.full_text.split("\n")
    assert "Chun Lin Hsieh -- CEO" in lines
    assert "Stefan Murry -- CFO" in lines
    # Each speaker's words follow their own line, in order.
    assert lines.index("Stefan Murry -- CFO") == lines.index("Chun Lin Hsieh -- CEO") + 2
    assert "Gross margin was 32% in the quarter." in lines


def test_an_empty_turn_list_is_unavailable_not_an_empty_transcript(monkeypatch):
    """
    Alpha Vantage answers 200 with an empty transcript for a quarter it
    does not have. An empty CallTranscript would flow downstream looking
    like a call where nothing was said.
    """
    from equity_analyzer.data_layer import transcript_source
    from equity_analyzer.data_layer.transcript_source import alpha_vantage_source

    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-test")
    monkeypatch.setattr(
        transcript_source.requests, "get",
        lambda *a, **k: _Response({"symbol": "AAOI", "transcript": []}),
    )
    with pytest.raises(TranscriptUnavailable, match="no usable 'transcript'"):
        alpha_vantage_source().fetch("AAOI", "0001158114", quarter="2026Q3")


def test_a_quota_refusal_is_named_as_such_not_as_a_missing_field(monkeypatch):
    """
    Alpha Vantage answers an exhausted quota with HTTP 200 and a prose
    message under "Information", carrying no data. Reported as "no
    usable 'transcript'", it points the reader at the field names when
    the real problem is the quota -- a confusion that cost a debugging
    round on the first live run.
    """
    from equity_analyzer.data_layer import transcript_source
    from equity_analyzer.data_layer.transcript_source import alpha_vantage_source

    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-test")
    monkeypatch.setattr(
        transcript_source.requests, "get",
        lambda *a, **k: _Response({"Information": "Thank you for using Alpha Vantage! "
                                                  "Our standard API rate limit is 25 requests per day."}),
    )
    with pytest.raises(TranscriptUnavailable, match="a refusé"):
        alpha_vantage_source().fetch("IBM", "", quarter="2025Q1")

    # and the vendor's own wording survives, so the cause is readable
    try:
        alpha_vantage_source().fetch("IBM", "", quarter="2025Q1")
    except TranscriptUnavailable as exc:
        assert "25 requests per day" in str(exc)


def test_a_premium_refusal_is_distinguishable_from_an_empty_quarter(monkeypatch):
    """
    Premium-only and "we have no transcript for that quarter" demand
    opposite responses -- abandon the vendor, or try another quarter.
    """
    from equity_analyzer.data_layer import transcript_source
    from equity_analyzer.data_layer.transcript_source import alpha_vantage_source

    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-test")

    monkeypatch.setattr(
        transcript_source.requests, "get",
        lambda *a, **k: _Response({"Information": "This is a premium endpoint."}),
    )
    with pytest.raises(TranscriptUnavailable, match="premium"):
        alpha_vantage_source().fetch("IBM", "", quarter="2025Q1")

    monkeypatch.setattr(
        transcript_source.requests, "get",
        lambda *a, **k: _Response({"symbol": "IBM", "quarter": "1990Q1", "transcript": []}),
    )
    with pytest.raises(TranscriptUnavailable, match="no usable 'transcript'"):
        alpha_vantage_source().fetch("IBM", "", quarter="1990Q1")


def test_the_real_ibm_payload_shape_parses(monkeypatch):
    """
    Pinned to the shape the live API actually returned on 2025Q1 --
    keys quarter/symbol/transcript, turns of content/sentiment/speaker/
    title -- so a future refactor cannot silently stop handling it.
    """
    from equity_analyzer.data_layer import transcript_source
    from equity_analyzer.data_layer.transcript_source import alpha_vantage_source

    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-test")
    monkeypatch.setattr(
        transcript_source.requests, "get",
        lambda *a, **k: _Response({"symbol": "IBM", "quarter": "2025Q1", "transcript": [
            {"speaker": "Operator", "title": "",
             "content": "Welcome. And thank you for standing by.", "sentiment": "0.6"},
            {"speaker": "Olympia McNerney", "title": "Global Head of Investor Relations",
             "content": "I'd like to welcome you to IBM's first quarter earnings.", "sentiment": "0.7"},
        ]}),
    )
    call = alpha_vantage_source().fetch("IBM", "", quarter="2025Q1")
    assert call.fiscal_period == "2025Q1"
    assert "Olympia McNerney -- Global Head of Investor Relations" in call.full_text
    # the extra `sentiment` field is ignored, not a parse failure
    assert "0.7" not in call.full_text


# --- the split, against wording seen on real calls -----------------------


def test_the_operators_opening_announcement_does_not_split_the_call():
    """
    Microsoft's operator opens with "A question and answer session will
    follow the formal presentation." That matches the boundary pattern
    exactly, and on the first live run it split the transcript at line
    two: three words of prepared remarks, the entire call filed as Q&A.
    An announcement points forward; a handover happens now.
    """
    turns = [
        "Operator",
        "Greetings, and welcome to the Microsoft Corporation Fiscal Year 2026 Second "
        "Quarter Earnings Conference Call. At this time, all participants are in a "
        "listen-only mode. A question and answer session will follow the formal presentation.",
        "Satya Nadella -- Chief Executive Officer",
        "Microsoft Cloud revenue grew 22% this quarter driven by Azure.",
        "Amy Hood -- Chief Financial Officer",
        "We expect operating margins to remain roughly flat next quarter.",
        "Operator",
        "We will now begin the question-and-answer session.",
        "Analyst -- Morgan Stanley",
        "Can you unpack the Azure growth drivers?",
    ]
    prepared, qa = split_prepared_from_qa("\n".join(turns))

    assert "Microsoft Cloud revenue grew 22%" in prepared
    assert "operating margins to remain roughly flat" in prepared
    assert "Can you unpack the Azure growth drivers?" in qa
    assert "Azure growth drivers" not in prepared


def test_other_ways_operators_announce_a_later_qa_are_also_ignored():
    for announcement in (
        "A question-and-answer session will be held at the end of the presentation.",
        "We will take questions and answers following the prepared remarks.",
        "There will be a question and answer session at the conclusion of today's call.",
        "Questions and answers will follow later in this call.",
    ):
        turns = ["Operator", announcement] + ["CEO", "Revenue grew."] * 8 + [
            "Operator", "We will now begin the question-and-answer session.",
            "Analyst", "My question.",
        ]
        prepared, qa = split_prepared_from_qa("\n".join(turns))
        assert "Revenue grew." in prepared, announcement
        assert "My question." in qa, announcement


def test_a_boundary_in_the_opening_lines_is_rejected_on_position_alone():
    """
    Second guard, independent of wording: prepared remarks are the bulk
    of an earnings call, never its first line. This catches an
    announcement phrased in a way the wording test misses.
    """
    turns = ["Operator", "Welcome. Q&A to be arranged."] + ["CEO", "Prepared content."] * 20
    prepared, qa = split_prepared_from_qa("\n".join(turns))
    assert qa is None
    assert "Prepared content." in prepared


def test_the_ibm_style_handover_still_splits_correctly():
    """
    IBM's operator did not announce the Q&A in the opening, so the
    original split worked there. The fix must not break it.
    """
    turns = ["Operator", "Welcome. And thank you for standing by. All participants "
             "are in a listen-only mode."] + ["CEO", "Revenue was strong."] * 10 + [
        "Operator", "We will now begin the question-and-answer session.",
        "Analyst", "What about margins?",
    ]
    prepared, qa = split_prepared_from_qa("\n".join(turns))
    assert "Revenue was strong." in prepared
    assert "What about margins?" in qa
