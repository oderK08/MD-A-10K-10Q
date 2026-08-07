"""
The earnings 8-K: the only SEC filing that appears on the day the news
does.

Why this exists at all. The 10-Q pipeline compares two consecutive
quarters, which is the right frame for news flow, but it is bounded by
when a 10-Q is FILED: typically days to weeks after the company reports.
Run the tool the morning after an earnings release and the newest 10-Q
still describes the PREVIOUS quarter. The 8-K under Item 2.02 ("Results
of Operations and Financial Condition") is filed the same day, usually
within minutes of the press release crossing the wire, and it carries:

  EX-99.1  the earnings press release: headline numbers, segment detail,
           and -- the part that moves prices -- forward guidance. Present
           on essentially every earnings 8-K.
  EX-99.2  sometimes the prepared remarks or the full call transcript.
           A minority of filers do this. When they do, it is free and it
           is the real thing; when they don't, nothing here can conjure
           it (see the module note at the bottom).

What this module deliberately does NOT do: guess. An 8-K is used for
dozens of unrelated events (a director resigning, a credit agreement, a
reverse split). Only Item 2.02 means "these are our results", so that is
what is filtered on, rather than assuming the most recent 8-K is an
earnings one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .cik_lookup import FilingNotFoundError, FilingRef

# SEC's item code for an earnings announcement. The submissions endpoint
# exposes a filing's items as a comma-separated string, e.g.
# "2.02,9.01" (9.01 is "Financial Statements and Exhibits", which an
# earnings 8-K almost always carries alongside 2.02 because it is what
# attaches the press release exhibit).
EARNINGS_ITEM = "2.02"

# Exhibit numbers, in the order a reader would want them. EX-99.1 is the
# press release; EX-99.2 is where prepared remarks or a transcript go
# when a filer publishes them.
_PRESS_RELEASE_TYPES = ("ex-99.1", "ex-99")
_SUPPLEMENT_TYPES = ("ex-99.2", "ex-99.3")

# A document worth reading is HTML or text. An earnings 8-K also carries
# XBRL sidecars, a cover-page XML, and often a logo image; none of those
# are prose.
_READABLE_SUFFIXES = (".htm", ".html", ".txt")

# Wording that marks an exhibit as an actual transcript rather than a
# press release. Checked against the exhibit's own description, which
# filers fill in freely -- hence several spellings.
_TRANSCRIPT_HINT_RE = re.compile(
    r"transcript|prepared\s+remarks|conference\s+call|earnings\s+call",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Exhibit:
    """One document filed under an accession."""
    document: str          # filename, e.g. "ex991pressrelease.htm"
    exhibit_type: str      # as the filer typed it, e.g. "EX-99.1"
    description: str       # free text the filer wrote, often empty


@dataclass(frozen=True)
class EarningsRelease:
    """
    An earnings 8-K, with its exhibits already classified.

    `transcript` is None far more often than not, and that is expected
    rather than a failure: most filers never attach one. `press_release`
    being None IS worth noticing -- an Item 2.02 filing with no readable
    exhibit means either an unusual filer or a classification gap here.
    """
    ref: FilingRef
    press_release: Optional[Exhibit]
    transcript: Optional[Exhibit]
    other_exhibits: list  # list[Exhibit], everything readable but unclassified


def _items_of(entry_items: str) -> set:
    return {item.strip() for item in (entry_items or "").split(",") if item.strip()}


def list_earnings_8ks(client, cik: str, limit: Optional[int] = None) -> list:
    """
    The company's 8-K filings that report results, most recent first.

    Filtered on Item 2.02 rather than on recency: an 8-K is the SEC's
    catch-all current-report form, and a company files them for a
    director's departure, a new credit facility, a delisting notice.
    Taking "the latest 8-K" and calling it earnings would silently
    analyse a governance announcement as if it were a quarter.

    Filers that omit the items field entirely are not silently dropped:
    with nothing to filter on, the 8-K is kept and the caller sees an
    unclassified exhibit list rather than an empty result.
    """
    submissions = client.fetch_submissions(cik)
    recent = submissions.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    items = recent.get("items", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    results = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        entry_items = items[i] if i < len(items) else ""
        if entry_items and EARNINGS_ITEM not in _items_of(entry_items):
            continue
        report_date = report_dates[i] if i < len(report_dates) else ""
        results.append(FilingRef(
            cik=cik,
            form_type=form,
            accession_number=accessions[i],
            filed_date=date.fromisoformat(filing_dates[i]),
            period_of_report=date.fromisoformat(report_date) if report_date else None,
            primary_document=primary_docs[i] if i < len(primary_docs) else "",
        ))
        if limit is not None and len(results) >= limit:
            break

    if not results:
        raise FilingNotFoundError(
            f"No earnings 8-K (Item {EARNINGS_ITEM}) found for CIK {cik} in "
            f"recent submissions."
        )
    return results


def _classify(exhibits: list) -> tuple:
    """
    Sorts readable exhibits into (press_release, transcript, other).

    Two signals, because filers are inconsistent about both. The exhibit
    NUMBER is the primary one (EX-99.1 is the press release by
    overwhelming convention). The DESCRIPTION overrides it: a filer who
    labels EX-99.1 "Earnings Call Transcript" means it, and the number
    convention should not outvote the filer's own words.
    """
    press_release = None
    transcript = None
    other = []

    for exhibit in exhibits:
        kind = (exhibit.exhibit_type or "").strip().lower()
        described_as_transcript = bool(_TRANSCRIPT_HINT_RE.search(exhibit.description or ""))

        if described_as_transcript and transcript is None:
            transcript = exhibit
        elif kind in _PRESS_RELEASE_TYPES and press_release is None:
            press_release = exhibit
        elif kind in _SUPPLEMENT_TYPES and transcript is None:
            transcript = exhibit
        else:
            other.append(exhibit)

    return press_release, transcript, other


def fetch_earnings_release(client, cik: str, ref: FilingRef) -> EarningsRelease:
    """
    Resolves one earnings 8-K's exhibit list, classified.

    Only the listing is fetched here, never the exhibit bodies: a caller
    that wants the press release should ask for that one document rather
    than pay for every attachment, which on an 8-K includes XBRL
    sidecars and sometimes a company logo.
    """
    index = client.fetch_filing_index(cik, ref.accession_number)
    items = index.get("directory", {}).get("item", [])

    readable = [
        Exhibit(
            document=entry.get("name", ""),
            exhibit_type=entry.get("type", ""),
            description=entry.get("description", "") or "",
        )
        for entry in items
        if entry.get("name", "").lower().endswith(_READABLE_SUFFIXES)
    ]
    press_release, transcript, other = _classify(readable)
    return EarningsRelease(
        ref=ref,
        press_release=press_release,
        transcript=transcript,
        other_exhibits=other,
    )


def latest_earnings_pair(client, cik: str) -> tuple:
    """
    The two most recent earnings 8-Ks, newest first, as EarningsRelease
    objects -- the pair a quarter-over-quarter comparison needs.

    Returns a 1-tuple when the company has only ever filed one. Raises
    FilingNotFoundError when it has none, which is a real answer for a
    filer that reports results only inside its 10-Q.
    """
    refs = list_earnings_8ks(client, cik, limit=2)
    return tuple(fetch_earnings_release(client, cik, ref) for ref in refs)


# A quarter's results are announced two to six weeks after it ends, and
# the periodic report follows within days of that. So an earnings 8-K
# belonging to the NEXT quarter cannot arrive until roughly a full
# quarter later. Ninety days is the quarter itself; forty five is a
# deliberately loose floor that no same-quarter release realistically
# clears while every next-quarter one does.
_MIN_DAYS_PAST_PERIOD_END = 45


def announces_a_newer_quarter(earnings_8k, *, filed_date, period_end) -> bool:
    """
    True when this earnings 8-K reports a quarter NEWER than the newest
    periodic report on file, given that report's filing date and period
    end.

    WHY THIS IS NOT A DATE COMPARISON, which is what a first version
    did and got wrong on the first real run. An 8-K's `period_of_report`
    is the date of the EVENT, not the end of a fiscal period: for an
    earnings release it is the day results were announced. Microsoft's
    quarter ended 30 June and its release went out on 29 July, so
    comparing 29 July against 30 June said "a newer quarter has been
    reported" about the release for that very quarter. The tool then
    searched a quarter that does not exist yet, spent a request finding
    out, printed a staleness warning that was false, and lost the
    consensus because it was looking for the wrong period.

    THE ORDER OF FILING IS THE REAL SIGNAL. A company announces results
    and then files the periodic report; it never files the 10-Q or 10-K
    for a quarter before announcing it. So an earnings 8-K filed AFTER
    the newest periodic report is announcing something that report does
    not cover. The elapsed-time floor below is a second, independent
    guard against a late amended release for a quarter already filed.

    BOTH CONDITIONS MUST HOLD, because the two mistakes are not equally
    expensive. Failing to step forward costs nothing: the ordinary
    backward search still finds the newest published call. Stepping
    forward wrongly costs a request, a false warning on page 1 and the
    whole consensus section. When in doubt, do not step.
    """
    if earnings_8k is None or filed_date is None or period_end is None:
        return False
    announced_on = earnings_8k.filed_date
    if announced_on is None:
        return False
    return (
        announced_on > filed_date
        and (announced_on - period_end).days > _MIN_DAYS_PAST_PERIOD_END
    )


__all__ = [
    "EARNINGS_ITEM",
    "announces_a_newer_quarter",
    "EarningsRelease",
    "Exhibit",
    "fetch_earnings_release",
    "latest_earnings_pair",
    "list_earnings_8ks",
]
