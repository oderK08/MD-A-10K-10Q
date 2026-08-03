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
    form_type: str,
    limit: Optional[int] = None,
) -> list[FilingRef]:
    """
    Returns filings of a given form_type (e.g. "10-K", "10-Q") for a CIK,
    most recent first, sourced from `filings.recent`.
    """
    submissions = client.fetch_submissions(cik)
    recent = submissions.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    results: list[FilingRef] = []
    for i, form in enumerate(forms):
        if form != form_type:
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
            f"No {form_type} filings found for CIK {cik} in recent "
            f"submissions.{hint}"
        )
    return results
