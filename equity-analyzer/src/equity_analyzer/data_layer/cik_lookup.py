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


@dataclass(frozen=True)
class ComparisonPair:
    """
    The two filings a quarterly report compares: the newest one and
    whatever it immediately follows.

    `prior_is_annual` is True at a fiscal-year boundary, where the
    quarter before Q1 is the 10-K. That is a real comparison, not a
    degraded one, but it is NOT like-for-like: a 10-K's MD&A discusses a
    full year and is written at length, so a Q1-vs-10-K diff shows far
    more movement than a Q3-vs-Q2 diff for reasons that have nothing to
    do with the business. The flag exists so the report can say so
    instead of letting the reader read a structural artefact as news.
    """
    current: FilingRef
    prior: Optional[FilingRef]
    prior_is_annual: bool


def _chronological_key(ref: FilingRef) -> date:
    """
    The date a filing's CONTENT belongs to (period of report), falling
    back to when it was filed. Ordering by filed_date alone would be
    wrong whenever a filer submits late or files an amended period out of
    order; the discussion in a filing is about its period, not about the
    day the lawyers finished.
    """
    return ref.period_of_report or ref.filed_date


def latest_quarterly_pair(client: EdgarClient, cik: str) -> ComparisonPair:
    """
    The most recent 10-Q, paired with the filing that immediately
    precedes it in time -- the previous 10-Q, or the 10-K when the newest
    quarter is a Q1.

    This is what makes the report about NEWS FLOW: what management said
    this quarter that it did not say last quarter, and what it stopped
    saying. Comparing two consecutive 10-Ks instead (the pipeline's
    original behaviour) means a company that just published its Q3 gets
    analysed on text that can be nine to twelve months old, and the
    quarter that just came out is never read at all.

    Raises FilingNotFoundError when the CIK has no 10-Q in
    `filings.recent`. Returns `prior=None` -- rather than raising --
    when it has exactly one filing on record: a current-quarter report
    with no comparison is degraded, but it is still a report, and the
    caller decides.
    """
    refs = list_filings(client, cik, ["10-Q", "10-K"])
    quarterlies = [r for r in refs if r.form_type == "10-Q"]
    if not quarterlies:
        raise FilingNotFoundError(
            f"No 10-Q filings found for CIK {cik} in recent submissions. "
            f"This report compares consecutive quarters, so a company with "
            f"no quarterly filings on record cannot be analysed this way."
        )

    current = max(quarterlies, key=_chronological_key)
    cutoff = _chronological_key(current)
    earlier = [
        r for r in refs
        if _chronological_key(r) < cutoff and r.accession_number != current.accession_number
    ]
    if not earlier:
        return ComparisonPair(current=current, prior=None, prior_is_annual=False)

    prior = max(earlier, key=_chronological_key)
    return ComparisonPair(
        current=current,
        prior=prior,
        prior_is_annual=prior.form_type == "10-K",
    )
