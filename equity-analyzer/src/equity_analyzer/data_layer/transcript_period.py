"""
Which quarter label to ask a transcript provider for.

The problem this closes. Alpha Vantage's `quarter` parameter looks
calendar-shaped ("2026Q2") but follows the ISSUER's fiscal calendar: a
live request for MSFT 2026Q2 returned the call for the quarter that
ended in December 2025. Ask with a calendar label for a June-year filer
and you get a call two quarters away from the one you meant -- no crash,
no empty section, just a confident analysis of the wrong period.

The fix costs nothing, because EDGAR already answers it. Every XBRL fact
carries `fy` and `fp`, the company's OWN fiscal year and period, and
the pipeline already extracts them into FinancialPeriod. That is the
same basis the transcript provider labels by, so the mapping is a
formatting job rather than a lookup -- and there is no fiscal-calendar
table to hardcode, get wrong, or maintain as companies change their year
end.

Nothing here is trusted blindly: `verify_against_declared` compares the
label we asked for against the period the company NAMES in the opening
of the call it sent back, so a mismatch surfaces instead of quietly
producing a wrong comparison.
"""

from __future__ import annotations

from typing import Optional

from .declared_period import read_declared_period

# EDGAR's `fp` for a 10-K is "FY". The annual report covers the fourth
# fiscal quarter, which is the call held alongside it -- companies
# present Q4 and full-year results on one call.
_ANNUAL = "FY"


class PeriodLabelUnavailable(Exception):
    """
    The filing does not carry enough fiscal information to build a
    label.

    Raised rather than falling back to the calendar quarter, which would
    be right for December-year filers and silently wrong for everyone
    else -- the exact failure this module exists to prevent.
    """


def alpha_vantage_label(fiscal_year: Optional[int], fiscal_period: Optional[str]) -> str:
    """
    "2026Q2" from EDGAR's own (fy, fp) pair.

    Deliberately NOT derived from a period end date. Two filers whose
    quarters end the same week can label them differently, and the label
    is what the provider indexes by.
    """
    if not fiscal_year or not fiscal_period:
        raise PeriodLabelUnavailable(
            f"EDGAR n'a pas de repère fiscal pour ce dépôt "
            f"(fy={fiscal_year!r}, fp={fiscal_period!r})"
        )

    period = str(fiscal_period).strip().upper()
    if period == _ANNUAL:
        return f"{int(fiscal_year)}Q4"
    if period.startswith("Q") and period[1:].isdigit():
        quarter = int(period[1:])
        if 1 <= quarter <= 4:
            return f"{int(fiscal_year)}Q{quarter}"
    raise PeriodLabelUnavailable(f"repère fiscal EDGAR non reconnu : {fiscal_period!r}")


def previous_label(label: str) -> str:
    """
    The quarter before, rolling the year back at Q1.

    The report compares consecutive quarters, so this is how the second
    half of the pair is addressed.
    """
    year, _, quarter = label.partition("Q")
    year, quarter = int(year), int(quarter)
    return f"{year - 1}Q4" if quarter == 1 else f"{year}Q{quarter - 1}"


def verify_against_declared(requested: str, transcript_text: str) -> Optional[str]:
    """
    None when the call we received is the one we asked for, otherwise a
    human-readable warning.

    Worth doing even though the label came from EDGAR: the provider may
    index a quarter differently, and a wrong pairing is invisible in the
    output. The company states the period out loud in the opening
    seconds of every call, so the check is free.
    """
    declared = read_declared_period(transcript_text)
    if declared is None:
        return f"période non déclarée dans l'ouverture du call (demandé {requested})"
    if declared.label != requested:
        return (
            f"demandé {requested} mais la société annonce {declared.label} "
            f"(« {declared.phrase} ») — appariement à vérifier avant de comparer"
        )
    return None


def find_latest_available(source, ticker, cik, start_label, max_back=4, pause=None):
    """
    The most recent quarter that ACTUALLY HAS a transcript, walking back
    from the one EDGAR says was filed last.

    Needed because those two are not the same thing, which the first
    live run made obvious: EDGAR had AAOI's Q2 2026 on file the same
    week the company reported, and the transcript provider did not have
    that call yet. Asking for exactly one quarter and giving up turned
    "published a few days ago" into "no transcript", which is wrong and
    discouraging in equal measure.

    Returns (transcript, label, quarters_back). `quarters_back` is how
    stale the answer is -- 0 means the newest filed quarter, 1 means the
    one before it -- and the caller is expected to show it rather than
    quietly present an older call as the latest.

    A REFUSAL STOPS THE WALK IMMEDIATELY. Quota exhaustion and a
    premium-only endpoint apply to every subsequent request too, so
    continuing would spend the rest of the day's budget to collect the
    same error four more times.
    """
    from .transcript_source import TranscriptRefused, TranscriptUnavailable

    label = start_label
    misses = []
    for attempt in range(max_back):
        if attempt and pause:
            pause()
        try:
            return source.fetch(ticker, cik, quarter=label), label, attempt
        except TranscriptRefused:
            raise
        except TranscriptUnavailable as exc:
            misses.append(f"{label} ({exc})")
            label = previous_label(label)

    raise TranscriptUnavailable(
        f"aucun transcript pour {ticker} sur les {max_back} derniers trimestres "
        f"testés — " + " ; ".join(misses)
    )


__all__ = [
    "PeriodLabelUnavailable",
    "alpha_vantage_label",
    "find_latest_available",
    "previous_label",
    "verify_against_declared",
]
