"""
Thin HTTP client for SEC EDGAR.

Rigor points enforced here (see design discussion):
1. SEC REQUIRES a descriptive User-Agent header (name + contact email).
   Requests without it get a 403. We refuse to construct the client without
   one rather than silently sending a generic header that will get blocked.
2. SEC's fair-access guidance caps traffic at ~10 requests/second. We
   throttle explicitly (default: 5 req/s, configurable) rather than relying
   on the caller to remember.
3. Two distinct endpoints, not to be confused:
     - data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json  -> structured financials
     - www.sec.gov/Archives/edgar/data/{cik}/...          -> raw filing text/HTML
   This client exposes both explicitly rather than one "get_filing" method
   that hides which one you're hitting.
4. The CIK -> ticker map is fetched from SEC's own official file
   (company_tickers.json), not scraped or guessed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests


SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
SEC_XBRL_COMPANYFACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


class EdgarClientError(Exception):
    pass


@dataclass
class EdgarClientConfig:
    user_agent: str  # MUST be "Company/App Name your-email@domain.com" per SEC guidance
    requests_per_second: float = 5.0
    timeout_seconds: float = 15.0


class EdgarClient:
    def __init__(self, config: EdgarClientConfig):
        if not config.user_agent or "@" not in config.user_agent:
            raise EdgarClientError(
                "A valid User-Agent with a contact email is required by SEC "
                "EDGAR (e.g. 'YourApp/1.0 you@example.com'). Requests without "
                "one will be rejected with HTTP 403."
            )
        self._config = config
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": config.user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self._min_interval = 1.0 / config.requests_per_second
        self._last_request_time: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_time = time.monotonic()

    def _get(self, url: str, expect_json: bool = True):
        self._throttle()
        resp = self._session.get(url, timeout=self._config.timeout_seconds)
        if resp.status_code == 403:
            raise EdgarClientError(
                f"403 from SEC EDGAR for {url}. Check that the User-Agent "
                f"header is set correctly (currently: "
                f"'{self._config.user_agent}')."
            )
        if resp.status_code == 404:
            raise EdgarClientError(f"404 from SEC EDGAR for {url} (not found).")
        resp.raise_for_status()
        return resp.json() if expect_json else resp.text

    # ------------------------------------------------------------------
    # CIK lookup
    # ------------------------------------------------------------------
    def fetch_ticker_to_cik_map(self) -> dict:
        """
        Returns the official SEC ticker->CIK map, e.g.
        {"AAPL": "0000320193", ...} (CIK zero-padded to 10 digits).
        """
        raw = self._get(SEC_TICKERS_URL)
        result = {}
        for entry in raw.values():
            ticker = entry["ticker"].upper()
            cik = str(entry["cik_str"]).zfill(10)
            result[ticker] = cik
        return result

    # ------------------------------------------------------------------
    # Structured financials (XBRL)
    # ------------------------------------------------------------------
    def fetch_company_facts(self, cik: str) -> dict:
        """
        Raw companyfacts JSON for a given zero-padded CIK.
        Contains every XBRL concept the company has ever reported, each
        with a list of values across filings/periods.
        """
        cik_padded = cik.zfill(10)
        url = f"{SEC_XBRL_COMPANYFACTS_BASE}/CIK{cik_padded}.json"
        return self._get(url)

    # ------------------------------------------------------------------
    # Filing index / submissions history
    # ------------------------------------------------------------------
    def fetch_submissions(self, cik: str) -> dict:
        """
        List of recent filings (form type, accession number, filing date,
        primary document filename) for a given CIK.
        """
        cik_padded = cik.zfill(10)
        url = f"{SEC_SUBMISSIONS_BASE}/CIK{cik_padded}.json"
        return self._get(url)

    # ------------------------------------------------------------------
    # Raw filing text (HTML)
    # ------------------------------------------------------------------
    def fetch_filing_document(self, cik: str, accession_number: str,
                               primary_document: str) -> str:
        """
        Fetches the raw HTML of the primary document (the 10-K/10-Q itself)
        for a given accession number.
        """
        cik_int = str(int(cik))  # archives path uses CIK without leading zeros
        accession_no_dashes = accession_number.replace("-", "")
        url = (f"{SEC_ARCHIVES_BASE}/{cik_int}/{accession_no_dashes}/"
               f"{primary_document}")
        return self._get(url, expect_json=False)
