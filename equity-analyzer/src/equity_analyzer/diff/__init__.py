"""
Module 3 -- Diff textuel: paragraph-level comparison of Item 1A (Risk
Factors) and Item 7 / Item 2 (MD&A) between two filings.

Operates on FilingTextSections from Module 1. Risk Factors diffs are
skipped explicitly (not silently reported as "no change") when the
current filing's section is the 10-Q boilerplate disclaimer; MD&A is
always diffed since it's rewritten every filing.
"""

from .errors import DiffError, MissingSectionError
from .grouped_diff import DiffGroup, GroupedTextDiffResult, diff_text_grouped
from .mdna import diff_mdna
from .risk_factors import RiskFactorsDiffResult, diff_risk_factors
from .text_diff import DiffSegment, TextDiffResult, diff_text

__all__ = [
    "DiffError",
    "MissingSectionError",
    "diff_mdna",
    "RiskFactorsDiffResult",
    "diff_risk_factors",
    "DiffSegment",
    "TextDiffResult",
    "diff_text",
    "DiffGroup",
    "GroupedTextDiffResult",
    "diff_text_grouped",
]
