"""
What the market expected, so the call can be read against something.

WHY THIS EXISTS. A verbatim reading of an earnings call, on its own,
answers "what did management say". It cannot answer the question a
portfolio manager actually has, which is "was that better or worse than
what was already priced in". The same sentence -- "we expect revenue
growth in the mid teens" -- is reassuring after a quarter that beat and
alarming after one that missed, and nothing in the transcript says which
of those happened.

The consensus EPS estimate is the cheapest honest anchor for that. It is
not a full picture of expectations (revenue consensus, segment guidance
and the buy-side's own whisper number all move a stock too) and this
module does not pretend otherwise -- but it is a published number, it is
attached to the exact quarter being read, and it is on the same free key
this project already uses for transcripts.

THE BEAT HISTORY MATTERS AS MUCH AS THE BEAT. A company that has beaten
by two cents every quarter for two years and beats by two cents again
has met expectations, not exceeded them; the same beat from a company
that missed three times running is a genuine inflection. So the last few
quarters travel with the current one rather than being dropped.

MATCHING THE RIGHT QUARTER is done on the fiscal period END DATE, not on
a fiscal label. Alpha Vantage dates these entries by calendar
`fiscalDateEnding`, EDGAR carries the same date on the filing, and the
two agree for every filer regardless of when its fiscal year ends. A
label ("2026Q2") would have to be translated, and translating it is the
exact failure this project already hit once (see transcript_period.py).

UNVERIFIED IN ONE RESPECT, stated rather than assumed: Alpha Vantage has
moved several endpoints to premium over time, and whether EARNINGS is on
the free tier could not be checked from the environment this was written
in (no network access to the vendor). A refusal is therefore handled as
a first-class outcome -- the report says expectations were unavailable
and why, and the reading still happens without them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import requests

from .alpha_vantage import ALPHA_VANTAGE_URL, soft_error, throttle

DEFAULT_TIMEOUT_SECONDS = 30.0

# How many quarters of beat/miss history travel with the current one.
# Four is one fiscal year: enough to tell a habitual small beat from a
# real inflection, short enough to stay one line on a two-page report.
HISTORY_QUARTERS = 4


class ExpectationsUnavailable(Exception):
    """
    No consensus figures for this company from this source.

    Raised rather than returned as an empty object, for the same reason
    the transcript source does it: "we could not get the estimate" and
    "there was no estimate" must never be confusable downstream, because
    a reader shown a blank line will assume the second.
    """


class ExpectationsRefused(ExpectationsUnavailable):
    """
    The provider declined to answer at all: quota, premium tier, bad key.

    Separate from a plain absence because only this one means every
    further request in the same run will fail too.
    """


@dataclass(frozen=True)
class QuarterExpectation:
    """One quarter's consensus estimate against what was actually reported."""

    fiscal_date_ending: date
    reported_date: Optional[date]
    estimated_eps: Optional[float]
    reported_eps: Optional[float]
    surprise: Optional[float]
    surprise_pct: Optional[float]

    @property
    def beat(self) -> Optional[bool]:
        """
        True if reported came in above consensus, None if either side is
        missing. Never False by default: an unknown result and a miss
        are opposite readings.
        """
        if self.reported_eps is None or self.estimated_eps is None:
            return None
        return self.reported_eps > self.estimated_eps

    @property
    def verdict(self) -> str:
        """One word, for a table cell and for the prompt."""
        beat = self.beat
        if beat is None:
            return "inconnu"
        if self.surprise_pct is not None and abs(self.surprise_pct) < 1.0:
            return "en ligne"
        return "au-dessus" if beat else "en dessous"


@dataclass(frozen=True)
class EarningsExpectations:
    """A company's consensus-vs-reported record, newest quarter first."""

    ticker: str
    quarters: list  # list[QuarterExpectation], newest first

    def index_of(self, period_end: date) -> Optional[int]:
        return next(
            (i for i, q in enumerate(self.quarters) if q.fiscal_date_ending == period_end),
            None,
        )

    def at(self, period_end: date, quarters_back: int = 0) -> Optional[QuarterExpectation]:
        """
        The entry for the quarter ending `period_end`, or `quarters_back`
        quarters before it.

        The offset exists because the call being read is not always the
        newest quarter on file, and it slips in BOTH directions. A
        company can file days after reporting, before the provider has
        published the call, so the call read is older (positive offset).
        It can also have reported a quarter it has not yet filed, so the
        call read is NEWER than anything on EDGAR (negative offset, and
        the list is newest first, so one quarter newer is one index
        lower). Either way the expectations shown have to be the ones
        for the call actually read, or the report measures a reading of
        one quarter against the consensus for another.

        A negative offset that runs off the top of the list returns
        None, which is the right answer: the provider has no consensus
        line for a quarter it has not published either.

        Returns None rather than the nearest entry when the anchor date
        is not in the list: guessing which quarter the caller meant is
        exactly the silent mispairing this offset exists to prevent.
        """
        anchor = self.index_of(period_end)
        if anchor is None:
            return None
        index = anchor + quarters_back
        return self.quarters[index] if 0 <= index < len(self.quarters) else None

    def history_before(self, quarter: QuarterExpectation, count: int = HISTORY_QUARTERS) -> list:
        """The `count` quarters preceding `quarter`, newest first."""
        try:
            start = self.quarters.index(quarter) + 1
        except ValueError:
            return []
        return self.quarters[start:start + count]


def _to_float(value) -> Optional[float]:
    """
    Alpha Vantage returns every number as a string, and uses the literal
    "None" for a quarter it has no estimate for. Both of those have to
    become a real None rather than crashing or, worse, becoming 0.0 --
    a zero EPS estimate is a meaningful number and would read as one.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "-", "nan"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_earnings_payload(ticker: str, payload) -> EarningsExpectations:
    """
    Turns the vendor's JSON into `EarningsExpectations`. Pure, so the
    whole shape can be tested without a network call.

    Entries with no usable period end date are dropped rather than kept
    with a placeholder: the end date is the key everything else is
    matched on, and an entry that cannot be matched can only ever be
    shown next to the wrong quarter.
    """
    refusal = soft_error(payload)
    if refusal:
        raise ExpectationsRefused(f"Alpha Vantage a refusé ({refusal})")

    if not isinstance(payload, dict):
        raise ExpectationsUnavailable("réponse Alpha Vantage de forme inattendue")

    raw = payload.get("quarterlyEarnings")
    if not isinstance(raw, list) or not raw:
        raise ExpectationsUnavailable(
            f"Alpha Vantage ne renvoie aucun historique trimestriel pour {ticker}"
        )

    quarters = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ending = _to_date(entry.get("fiscalDateEnding"))
        if ending is None:
            continue
        quarters.append(
            QuarterExpectation(
                fiscal_date_ending=ending,
                reported_date=_to_date(entry.get("reportedDate")),
                estimated_eps=_to_float(entry.get("estimatedEPS")),
                reported_eps=_to_float(entry.get("reportedEPS")),
                surprise=_to_float(entry.get("surprise")),
                surprise_pct=_to_float(entry.get("surprisePercentage")),
            )
        )

    if not quarters:
        raise ExpectationsUnavailable(
            f"aucune ligne trimestrielle datée exploitable pour {ticker}"
        )

    # Sorted here rather than trusted from the vendor: everything
    # downstream steps through this list by index (`at`, `history_before`),
    # so its order is load-bearing and must not depend on a field the
    # vendor could reorder.
    quarters.sort(key=lambda q: q.fiscal_date_ending, reverse=True)
    return EarningsExpectations(ticker=ticker, quarters=quarters)


def fetch_earnings_expectations(
    ticker: str,
    *,
    api_key_env: str = "ALPHAVANTAGE_API_KEY",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> EarningsExpectations:
    """
    One request, the same free key as the transcripts.

    Costs one of the free tier's 25 daily requests. Worth it: without it
    the reading has nothing to measure the quarter against, which is the
    difference between "management said growth would be mid teens" and
    "management guided below what the street had".
    """
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ExpectationsUnavailable(f"pas de clé Alpha Vantage dans ${api_key_env}")

    # Shared with the transcript request: the rate limit belongs to the
    # provider, not to this module (see data_layer/alpha_vantage.py).
    throttle()
    try:
        response = requests.get(
            ALPHA_VANTAGE_URL,
            params={"function": "EARNINGS", "symbol": ticker, "apikey": api_key},
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise ExpectationsUnavailable(f"erreur réseau vers Alpha Vantage : {exc}") from exc

    if response.status_code != 200:
        raise ExpectationsUnavailable(
            f"Alpha Vantage a renvoyé HTTP {response.status_code} : {response.text[:200]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExpectationsUnavailable(f"Alpha Vantage a renvoyé du non-JSON : {exc}") from exc

    return parse_earnings_payload(ticker, payload)


__all__ = [
    "EarningsExpectations",
    "ExpectationsRefused",
    "ExpectationsUnavailable",
    "HISTORY_QUARTERS",
    "QuarterExpectation",
    "fetch_earnings_expectations",
    "parse_earnings_payload",
]
