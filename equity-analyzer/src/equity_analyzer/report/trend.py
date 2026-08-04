"""
Multi-year trend analysis: runs the existing single-period report
builder across a sliding window of consecutive filings, instead of just
one current/prior pair. A single Piotroski F-Score of 7/9 doesn't mean
much on its own -- was it 4/9 two years ago and climbing, or 9/9 and
declining? The trend is where the signal actually is.

Deliberately does NOT attempt sector/peer comparison. That needs a
fundamentally different data-fetching pattern (several companies' data
at once, not one company's history) and, more importantly, no way to
validate the result against real data from this dev environment (still
no network access to data.sec.gov as of this module). Shipping an
unvalidated peer-comparison feature would contradict the real-data
testing discipline this project has followed since the Module 3
paragraph-splitting bug -- better to ship what can actually be checked
against a live GitHub Actions run, and revisit sector comparison once
that's possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..data_layer.models import Filing
from ..sentiment.lm_dictionary import LMDictionary
from .report_data import ReportData, build_report_data


@dataclass(frozen=True)
class TrendPoint:
    fiscal_year: int
    filing: Filing
    report: ReportData  # this year vs. the immediately preceding one in the series


@dataclass(frozen=True)
class TrendAnalysis:
    points: list  # list[TrendPoint], ordered oldest -> newest


def build_trend_analysis(
    filings: list,
    lm_dictionary: Optional[LMDictionary] = None,
) -> TrendAnalysis:
    """
    `filings` must be ordered OLDEST -> NEWEST (e.g. five consecutive
    fiscal years' 10-Ks for the same company) and strictly increasing by
    fiscal_year -- raises ValueError otherwise, since a trend built on
    out-of-order or duplicate years would silently mislead.

    Each point's report compares that filing to the filing immediately
    before it IN THE LIST -- never skipping a year -- so Beneish M /
    Piotroski F / the text diffs are always a true year-over-year pair,
    exactly like build_report_data's normal single-pair usage. The
    oldest filing has no prior, so its report only has Altman Z and
    sentiment available (build_report_data's ordinary graceful
    degradation) -- same as any first-ever analysis of a company would.
    """
    if len(filings) < 2:
        raise ValueError(
            "build_trend_analysis needs at least 2 filings to show a "
            "trend; use build_report_data directly for a single period."
        )
    for earlier, later in zip(filings, filings[1:]):
        if later.fiscal_year <= earlier.fiscal_year:
            raise ValueError(
                f"filings must be ordered oldest -> newest by strictly "
                f"increasing fiscal_year; got {earlier.fiscal_year} "
                f"followed by {later.fiscal_year}."
            )

    points = []
    for i, filing in enumerate(filings):
        prior = filings[i - 1] if i > 0 else None
        report = build_report_data(filing, prior, lm_dictionary)
        points.append(TrendPoint(fiscal_year=filing.fiscal_year, filing=filing, report=report))
    return TrendAnalysis(points=points)
