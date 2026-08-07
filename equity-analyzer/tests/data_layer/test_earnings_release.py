"""
Tests for the earnings 8-K: locating it, and reading its exhibits.

This is the only SEC filing that appears on the day the news does. The
10-Q pipeline is bounded by when a 10-Q is FILED, days to weeks after
the company reports, so the morning after an earnings release the newest
10-Q still describes the previous quarter.
"""

import pytest

from equity_analyzer.data_layer.cik_lookup import FilingNotFoundError
from equity_analyzer.data_layer.earnings_release import (
    fetch_earnings_release,
    latest_earnings_pair,
    list_earnings_8ks,
)
from equity_analyzer.data_layer.earnings_text import extract_earnings_text

CIK = "0001158114"


class FakeClient:
    def __init__(self, submissions, indexes=None):
        self._submissions = submissions
        self._indexes = indexes or {}

    def fetch_submissions(self, cik):
        return self._submissions

    def fetch_filing_index(self, cik, accession_number):
        return self._indexes[accession_number]


def _submissions(rows):
    """rows: list of (form, items, accession, filing_date, report_date)."""
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "items": [r[1] for r in rows],
                "accessionNumber": [r[2] for r in rows],
                "filingDate": [r[3] for r in rows],
                "reportDate": [r[4] for r in rows],
                "primaryDocument": [f"{r[2]}.htm" for r in rows],
            }
        }
    }


def _index(*items):
    return {"directory": {"item": list(items)}}


def _doc(name, type_, description=""):
    return {"name": name, "type": type_, "description": description}


# --- finding the right 8-K ----------------------------------------------


def test_only_item_2_02_filings_count_as_earnings():
    """
    An 8-K is the SEC's catch-all current report: a director resigning, a
    credit agreement, a reverse split. Taking "the latest 8-K" would
    silently analyse a governance announcement as if it were a quarter.
    """
    client = FakeClient(_submissions([
        ("8-K", "5.02", "acc-director", "2026-08-05", "2026-08-04"),
        ("8-K", "2.02,9.01", "acc-earnings", "2026-08-06", "2026-08-06"),
        ("8-K", "1.01", "acc-credit", "2026-07-01", "2026-07-01"),
        ("10-Q", "", "acc-10q", "2026-05-10", "2026-03-31"),
    ]))
    refs = list_earnings_8ks(client, CIK)
    assert [r.accession_number for r in refs] == ["acc-earnings"]


def test_an_8k_with_no_items_field_is_kept_rather_than_dropped():
    """
    With nothing to filter on, dropping would lose a real earnings
    release silently. Keeping it means the caller sees an unclassified
    exhibit list instead of an empty result.
    """
    client = FakeClient(_submissions([
        ("8-K", "", "acc-unknown", "2026-08-06", "2026-08-06"),
    ]))
    assert [r.accession_number for r in list_earnings_8ks(client, CIK)] == ["acc-unknown"]


def test_a_filer_that_never_reports_via_8k_raises_rather_than_guessing():
    client = FakeClient(_submissions([
        ("10-Q", "", "acc-10q", "2026-05-10", "2026-03-31"),
    ]))
    with pytest.raises(FilingNotFoundError, match="No earnings 8-K"):
        list_earnings_8ks(client, CIK)


# --- reading its exhibits ------------------------------------------------


def test_the_press_release_exhibit_is_found_not_the_8k_body():
    """
    An earnings 8-K's own primary document is three sentences of
    cross-reference; the news is in EX-99.1. The submissions endpoint
    names only the primary document, so without the index listing the
    press release is unreachable.
    """
    client = FakeClient(
        _submissions([("8-K", "2.02,9.01", "acc-e", "2026-08-06", "2026-08-06")]),
        {"acc-e": _index(
            _doc("form8k.htm", "8-K"),
            _doc("ex991.htm", "EX-99.1", "Press Release dated August 6, 2026"),
            _doc("logo.jpg", "GRAPHIC"),
            _doc("form8k_htm.xml", "XML"),
        )},
    )
    release = fetch_earnings_release(client, CIK, list_earnings_8ks(client, CIK)[0])
    assert release.press_release.document == "ex991.htm"
    assert release.transcript is None
    # the logo and the XBRL sidecar are not prose and are not offered as one
    assert all(e.document.endswith((".htm", ".html", ".txt")) for e in release.other_exhibits)


def test_a_transcript_exhibit_is_recognised_when_the_filer_attaches_one():
    client = FakeClient(
        _submissions([("8-K", "2.02", "acc-e", "2026-08-06", "2026-08-06")]),
        {"acc-e": _index(
            _doc("form8k.htm", "8-K"),
            _doc("ex991.htm", "EX-99.1", "Press Release"),
            _doc("ex992.htm", "EX-99.2", "Earnings Call Transcript"),
        )},
    )
    release = fetch_earnings_release(client, CIK, list_earnings_8ks(client, CIK)[0])
    assert release.press_release.document == "ex991.htm"
    assert release.transcript.document == "ex992.htm"


def test_the_filers_own_description_outranks_the_exhibit_number():
    """
    EX-99.1 is the press release by convention, but a filer who labels it
    "Earnings Call Transcript" means it. Convention must not outvote the
    filer's own words.
    """
    client = FakeClient(
        _submissions([("8-K", "2.02", "acc-e", "2026-08-06", "2026-08-06")]),
        {"acc-e": _index(
            _doc("ex991.htm", "EX-99.1", "Transcript of earnings conference call"),
        )},
    )
    release = fetch_earnings_release(client, CIK, list_earnings_8ks(client, CIK)[0])
    assert release.transcript.document == "ex991.htm"
    assert release.press_release is None


def test_latest_earnings_pair_returns_the_two_most_recent():
    indexes = {
        acc: _index(_doc("ex991.htm", "EX-99.1", "Press Release"))
        for acc in ("acc-q3", "acc-q2", "acc-q1")
    }
    client = FakeClient(
        _submissions([
            ("8-K", "2.02", "acc-q3", "2026-08-06", "2026-08-06"),
            ("8-K", "2.02", "acc-q2", "2026-05-07", "2026-05-07"),
            ("8-K", "2.02", "acc-q1", "2026-02-05", "2026-02-05"),
        ]),
        indexes,
    )
    pair = latest_earnings_pair(client, CIK)
    assert [r.ref.accession_number for r in pair] == ["acc-q3", "acc-q2"]


# --- turning an exhibit into text worth diffing --------------------------

_RELEASE_HTML = """
<html><body>
<p>Applied Optoelectronics Reports Third Quarter 2026 Results</p>
<p>Revenue for the third quarter was $217.6 million, up 12% year over year,
driven by continued strength in data center demand.</p>
<p>Business Outlook</p>
<p>For the fourth quarter of 2026, the Company expects revenue in the range
of $240 million to $250 million.</p>
<p>Non-GAAP gross margin is expected to be in the range of 32% to 34%.</p>
<p>Conference Call</p>
<p>The Company will host a conference call at 4:30 p.m. Eastern Time.</p>
<table>
<tr><td>Revenue</td><td>217,646</td><td>194,320</td><td>12.0%</td></tr>
<tr><td>Cost of goods sold</td><td>(151,200)</td><td>(140,110)</td><td>7.9%</td></tr>
<tr><td>Net loss</td><td>(56,048)</td><td>(61,330)</td><td>(8.6)%</td></tr>
</table>
</body></html>
"""


def test_financial_statement_tables_are_dropped_from_the_narrative():
    """
    Flattened to text, a statement row is an unreadable run of digits
    that diffs as changed every single quarter -- true, and useless. The
    same numbers are already in the report from XBRL, where they carry a
    period and a concept name.
    """
    result = extract_earnings_text(_RELEASE_HTML)
    assert result.dropped_table_lines >= 3
    assert "151,200" not in result.narrative
    # ...while the prose that quotes figures in a sentence is untouched
    assert "up 12% year over year" in result.narrative


def test_the_outlook_section_is_singled_out():
    """
    Everything else restates a quarter the market has already priced.
    Guidance is the genuinely new information, and quarter over quarter
    it is the most comparable text in the document: filers write it to a
    template and change the numbers.
    """
    result = extract_earnings_text(_RELEASE_HTML)
    assert result.outlook is not None
    assert "$240 million to $250 million" in result.outlook
    assert "32% to 34%" in result.outlook
    # it stops at the next conventional heading
    assert "conference call" not in result.outlook.lower()
    # and it is still present in the narrative: a spotlight, not a slice
    assert "$240 million to $250 million" in result.narrative


def test_a_release_with_no_outlook_section_says_so_rather_than_guessing():
    html = "<p>Results</p><p>Revenue was $10 million.</p><p>Conference Call</p><p>Dial in.</p>"
    result = extract_earnings_text(html)
    assert result.outlook is None
    assert "Revenue was $10 million." in result.narrative


def test_a_prose_line_wrapped_across_block_tags_is_not_mistaken_for_a_table_row():
    """
    Financial printers wrap one paragraph across several block elements,
    so a real sentence can arrive without its closing period. Dropping
    those as table rows would silently delete the narrative -- the exact
    content this module exists to keep.
    """
    html = (
        "<div>Revenue for the third quarter was $217.6 million, up 12%</div>"
        "<div>year over year, driven by data center demand</div>"
    )
    result = extract_earnings_text(html)
    assert result.dropped_table_lines == 0
    assert "up 12%" in result.narrative
    assert "data center demand" in result.narrative


def test_a_guidance_line_that_reads_like_a_row_is_kept():
    """
    Guidance is often set as a short labelled line with two figures --
    structurally a table row, and the single most valuable line in the
    document. It survives because it ends as a sentence.
    """
    html = (
        "<p>Business Outlook</p>"
        "<p>Revenue: $240 million to $250 million.</p>"
        "<p>Non-GAAP gross margin: 32% to 34%.</p>"
    )
    result = extract_earnings_text(html)
    assert result.dropped_table_lines == 0
    assert result.outlook is not None
    assert "$240 million to $250 million." in result.outlook


def test_a_terse_statement_row_with_a_long_label_is_still_dropped():
    """
    The case a share-of-letters test got wrong: "Cost of goods sold" is
    over half letters, so the row read as prose and survived.
    """
    from equity_analyzer.data_layer.earnings_text import _is_table_row

    assert _is_table_row("Cost of goods sold (151,200) (140,110) 7.9%")
    assert _is_table_row("Revenue 217,646 194,320 12.0%")
    assert not _is_table_row(
        "Revenue for the third quarter was $217.6 million, up 12% year over year."
    )
