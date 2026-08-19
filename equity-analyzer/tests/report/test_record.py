"""
The machine-readable twin of a report.

WHAT IT IS FOR: a sector view asked of many reports at once, which no
tool gets from PDFs. So the record must be JSON-able, robust to missing
pieces (it runs after the PDF is already written and must never raise),
and must surface the fields a synthesis groups on without re-parsing the
reading.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from equity_analyzer.report.record import report_record
from equity_analyzer.report.report_data import build_call_report

from .factories import make_analysis, make_expectation, make_transcript
from .test_call_report import _qa

READING = (
    "## Verdict\nPlutot bearish, la marge se degrade.\n\n"
    "## Les declarations cles\n\"capex over $50 billion\"\n"
)


def _report(transcript=None, **overrides):
    kwargs = dict(call_quarter="2026Q2",
                  generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    kwargs.update(overrides)
    return build_call_report(
        "SAP", "SAP SE", "0001",
        transcript if transcript is not None
        else make_transcript(prepared="x " * 40, qa="q " * 40),
        make_analysis(text=READING),
        **kwargs,
    )


def test_the_record_is_json_serialisable():
    """The whole point is that a later pass loads a dozen of these."""
    record = report_record(_report())
    round_tripped = json.loads(json.dumps(record, ensure_ascii=False))
    assert round_tripped["ticker"] == "SAP"


def test_the_verdict_is_pulled_out_as_a_field():
    """
    A sector view sorts and groups on the headline. Making it re-parse
    the reading paragraph for every company would be the wrong shape.
    """
    assert report_record(_report())["verdict"] == "Plutot bearish, la marge se degrade."


def test_the_full_reading_is_kept_too():
    """For the parts no schema anticipates."""
    assert "capex over $50 billion" in report_record(_report())["lecture"]


def test_the_consensus_is_structured_when_present():
    record = report_record(_report(expectation=make_expectation()))
    assert record["attentes"]["disponible"] is True
    assert record["attentes"]["bpa_publie"] is not None


def test_an_absent_consensus_is_recorded_as_unavailable_with_its_reason():
    record = report_record(_report(expectations_reason="hors périmètre SEC"))
    assert record["attentes"]["disponible"] is False
    assert record["attentes"]["raison"] == "hors périmètre SEC"


def test_the_qa_counts_and_findings_are_both_there():
    import dataclasses

    record = report_record(dataclasses.replace(_report(), qa_analysis=_qa()))
    assert record["qa"]["esquives_graves"] >= 1
    assert isinstance(record["qa"]["esquives"], list) and record["qa"]["esquives"]


def test_no_qa_page_records_qa_as_none():
    assert report_record(_report())["qa"] is None


def test_an_international_report_is_marked_as_such():
    assert report_record(_report(international=True))["international"] is True


def test_the_results_date_is_the_call_date_when_known():
    """
    A sector view compares freshness across issuers, and a fiscal label
    cannot do that. The call date is the truth of when the results landed.
    """
    record = report_record(
        _report(transcript=make_transcript(
            prepared="x " * 40, qa="q " * 40, call_date=date(2026, 7, 30))))
    assert record["date_resultats"] == "2026-07-30"


def test_the_results_date_falls_back_to_the_release_date():
    """No call date, but the consensus carries when the numbers were
    reported: that still dates the results."""
    record = report_record(_report(
        expectation=make_expectation(reported_date=date(2026, 7, 28))))
    assert record["date_resultats"] == "2026-07-28"


def test_the_results_date_is_none_when_nothing_dates_the_results():
    """Neither a call date nor a consensus: the field is null and a sector
    view falls back to when the report was generated."""
    assert report_record(_report())["date_resultats"] is None


def test_a_reading_without_a_verdict_line_yields_an_empty_verdict_not_a_crash():
    """
    Best effort: a model that ignored the template must not break the
    archive of a report that was otherwise fine.
    """
    from equity_analyzer.report.record import _verdict_line
    assert _verdict_line("pas de titre ici") == ""
    assert _verdict_line("") == ""
