"""
Item 1A (Risk Factors) diff, with the 10-Q boilerplate case handled
explicitly rather than silently.

A 10-Q's Part II Item 1A very often just states there have been no
material changes from the last 10-K (flagged by Module 1 as
`is_risk_factors_boilerplate`). Diffing that one-sentence disclaimer
against a prior period would report "100% similar, nothing changed" --
which LOOKS like a real "no risk changes" signal but isn't one: it's a
disclosure shortcut, not a substantive comparison, and the actual risk
factors on file are still whatever the most recent 10-K said. We refuse
to compute a diff in that case and say so explicitly (`skipped=True`,
`skip_reason=...`) rather than return a misleadingly clean diff.

The symmetric case -- prior period was boilerplate, current period is a
genuine rewrite (e.g. the annual 10-K just came out with real content) --
is NOT skipped: that is a real, informative transition, so we diff it
normally and simply note `prior_was_boilerplate=True` for context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..data_layer.models import FilingTextSections
from .errors import MissingSectionError
from .grouped_diff import GroupedTextDiffResult, diff_text_grouped


@dataclass(frozen=True)
class RiskFactorsDiffResult:
    skipped: bool
    skip_reason: Optional[str]
    diff: Optional[GroupedTextDiffResult]
    prior_was_boilerplate: bool


def diff_risk_factors(
    current: FilingTextSections,
    prior: FilingTextSections,
) -> RiskFactorsDiffResult:
    if current.item_1a_risk_factors is None:
        raise MissingSectionError(
            "current filing has no extracted Item 1A Risk Factors section."
        )
    if prior.item_1a_risk_factors is None:
        raise MissingSectionError(
            "prior filing has no extracted Item 1A Risk Factors section."
        )

    prior_was_boilerplate = prior.is_risk_factors_boilerplate

    if current.is_risk_factors_boilerplate:
        return RiskFactorsDiffResult(
            skipped=True,
            skip_reason=(
                "current filing's Item 1A is the 10-Q 'no material changes' "
                "boilerplate -- there is no substantive text to diff this "
                "quarter. This does not itself mean risk factors are "
                "unchanged; check the most recent 10-K's Item 1A for what's "
                "actually on file."
            ),
            diff=None,
            prior_was_boilerplate=prior_was_boilerplate,
        )

    return RiskFactorsDiffResult(
        skipped=False,
        skip_reason=None,
        diff=diff_text_grouped(prior.item_1a_risk_factors, current.item_1a_risk_factors),
        prior_was_boilerplate=prior_was_boilerplate,
    )
