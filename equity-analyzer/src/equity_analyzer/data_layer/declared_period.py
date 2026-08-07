"""
Reading, out of a transcript, which quarter the company says it is
reporting.

This exists to settle a mapping that cannot be assumed. Alpha Vantage's
`quarter` parameter looks calendar-shaped ("2026Q2") but on the first
live Microsoft call a request for 2026Q2 returned the call for the
fiscal quarter that ended in DECEMBER 2025 -- Microsoft's fiscal Q2 of
its fiscal 2026. So the parameter follows the issuer's own fiscal
calendar, not the civil one.

Why that matters more than it sounds. The report compares two
consecutive quarters. If the mapping is wrong by two quarters for a
June-year filer and by nearly a full label-year for a January-year
filer, the tool will faithfully diff two periods that do not belong
together and present the result with complete confidence. There is no
crash, no empty section, nothing that looks wrong -- just an analysis of
the wrong pair. Of six names on the watchlist, three fiscal calendars
are in play, so this is not an edge case.

Rather than encode a fiscal-year table per company and trust it, this
reads what the company ITSELF says in the opening seconds of the call:
the operator and the CFO both name the period, out loud, every time.
Comparing that against what was requested turns the mapping from an
assumption into a measurement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4,
}

# "Fiscal Year 2026 Second Quarter", "second quarter of fiscal 2026",
# "fiscal second quarter 2026", "Q2 fiscal 2026", "first quarter 2026".
# The word "fiscal" is captured when present, because its presence is
# itself the signal that the label is not a calendar one.
_PATTERNS = (
    re.compile(
        r"(?P<fiscal>fiscal(?:\s+year)?)\s+(?P<year>20\d{2})\s+"
        r"(?P<ordinal>first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<ordinal>first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s+"
        r"(?:of\s+|for\s+)?(?:(?P<fiscal>fiscal)(?:\s+year)?\s+)?(?P<year>20\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<fiscal>fiscal\s+)?(?P<ordinal>first|second|third|fourth)\s+quarter\s+"
        r"(?:of\s+)?fiscal(?:\s+year)?\s+(?P<year>20\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bQ(?P<ordinal>[1-4])\s+(?:of\s+)?(?:(?P<fiscal>fiscal)(?:\s+year)?\s+)?(?P<year>20\d{2})",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class DeclaredPeriod:
    """What the company called the period, in its own words."""
    year: int
    quarter: int
    says_fiscal: bool          # the label carried the word "fiscal"
    phrase: str                # the matched text, for the audit trail

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}"


def read_declared_period(text: str, search_chars: int = 1200) -> Optional[DeclaredPeriod]:
    """
    The period named in the opening of a call, or None.

    Only the opening is searched. Later in the call, speakers reference
    other periods constantly -- prior-year comparatives, guidance for
    the quarter ahead -- and any of those would match just as well. The
    operator's greeting and the IR host's introduction both name the
    current period within the first few hundred words, so a bounded
    search is both sufficient and much safer than a whole-document one.
    """
    head = text[:search_chars]
    for pattern in _PATTERNS:
        match = pattern.search(head)
        if not match:
            continue
        ordinal = match.group("ordinal").lower()
        quarter = _ORDINALS.get(ordinal)
        if quarter is None and ordinal.isdigit():
            quarter = int(ordinal)
        if quarter is None:
            continue
        return DeclaredPeriod(
            year=int(match.group("year")),
            quarter=quarter,
            says_fiscal=bool(match.groupdict().get("fiscal")),
            phrase=match.group(0).strip(),
        )
    return None


__all__ = ["DeclaredPeriod", "read_declared_period"]
