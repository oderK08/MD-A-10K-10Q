"""
CIK lookup and filing-index helpers, built on top of EdgarClient.

Handles a real quirk of the SEC submissions endpoint: filings are split
between `filings.recent` (last ~1000 filings, inline in the JSON) and
`filings.files` (older filings, referenced as separate paginated JSON
files). Most advisory use cases only need recent filings (10-K/10-Q go
back a few years at most in `recent`), so we support `recent` fully and
raise a clear, explicit error if a requested filing predates it rather
than silently returning "not found".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .edgar_client import EdgarClient


class FilingNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class FilingRef:
    """A lightweight reference to one filing, before we fetch its content."""
    cik: str
    form_type: str
    accession_number: str
    filed_date: date
    period_of_report: Optional[date]
    primary_document: str
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None


class CikLookup:
    """Caches the ticker->CIK map for the lifetime of the instance."""

    def __init__(self, client: EdgarClient):
        self._client = client
        self._map: Optional[dict] = None

    def resolve(self, ticker: str) -> str:
        if self._map is None:
            self._map = self._client.fetch_ticker_to_cik_map()
        ticker_upper = ticker.upper()
        if ticker_upper not in self._map:
            raise FilingNotFoundError(
                f"Ticker '{ticker}' not found in SEC's official "
                f"company_tickers.json mapping."
            )
        return self._map[ticker_upper]


def list_filings(
    client: EdgarClient,
    cik: str,
    form_type,
    limit: Optional[int] = None,
) -> list[FilingRef]:
    """
    Returns filings of a given form type for a CIK, most recent first,
    sourced from `filings.recent`.

    `form_type` is either one form ("10-K") or several (["10-Q", "10-K"]),
    the latter for building a chronological sequence that crosses the
    annual boundary -- a company's Q1 is preceded by its 10-K, not by
    another 10-Q. With several forms the result is one list ordered by
    recency across all of them, not grouped by form.
    """
    wanted = {form_type} if isinstance(form_type, str) else set(form_type)
    submissions = client.fetch_submissions(cik)
    recent = submissions.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    results: list[FilingRef] = []
    for i, form in enumerate(forms):
        if form not in wanted:
            continue
        period_of_report = (
            date.fromisoformat(report_dates[i]) if report_dates[i] else None
        )
        results.append(FilingRef(
            cik=cik,
            form_type=form,
            accession_number=accession_numbers[i],
            filed_date=date.fromisoformat(filing_dates[i]),
            period_of_report=period_of_report,
            primary_document=primary_docs[i],
        ))
        if limit is not None and len(results) >= limit:
            break

    if not results:
        older_files = submissions.get("filings", {}).get("files", [])
        hint = (
            " This CIK has older filings paginated outside `filings.recent` "
            "that this function does not fetch yet; the filing you want may "
            "be among those." if older_files else ""
        )
        raise FilingNotFoundError(
            f"No {'/'.join(sorted(wanted))} filings found for CIK {cik} in "
            f"recent submissions.{hint}"
        )
    return results


def _chronological_key(ref: FilingRef) -> date:
    """
    The date a filing's CONTENT belongs to (period of report), falling
    back to when it was filed. Ordering by filed_date alone would be
    wrong whenever a filer submits late or files an amended period out
    of order; what a filing discusses is its period, not the day the
    lawyers finished.
    """
    return ref.period_of_report or ref.filed_date


def latest_reported_period(client: EdgarClient, cik: str) -> FilingRef:
    """
    The most recent period the company has filed a periodic report for,
    whether that report is a 10-Q or a 10-K.

    BOTH FORMS, and that is the whole point of this function. A fiscal
    year has four quarters but only three 10-Qs: the fourth quarter is
    reported inside the 10-K, alongside the full year. Selecting "the
    newest 10-Q" therefore skips one quarter in four for every company
    on earth, and skips it silently, because a Q3 filing is a perfectly
    valid filing and nothing downstream can tell it is the wrong one.

    Found on a real MSFT run. Microsoft's fiscal year ends in June, so
    its Q4 ends 30 June and is reported in the 10-K filed in late July.
    Asking for the newest 10-Q in August returned the quarter ended 31
    March, and the tool went off and read an earnings call from April
    while presenting it as the latest one.

    Ordered by period of report rather than by filing date: a company
    that files late still belongs to its own quarter.
    """
    refs = list_filings(client, cik, ["10-Q", "10-K"])
    return max(refs, key=_chronological_key)
