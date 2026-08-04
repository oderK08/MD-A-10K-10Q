"""
Sentiment scoring wired directly to FilingTextSections (Module 1), mirroring
the diff module's item-specific wrappers (Module 3) so both text-based
modules share the same shape: a plain MD&A function, and a Risk Factors
function that explicitly skips 10-Q boilerplate rather than scoring it.

Scoring the boilerplate "no material changes" disclaimer would produce a
technically valid but meaningless SentimentResult -- a couple of neutral
sentences, not the tone of the actual risk factors on file (those are
still whatever the most recent 10-K said). We refuse to score it and say
why, the same way Module 3 refuses to diff it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..data_layer.models import FilingTextSections
from .errors import MissingSectionError
from .lm_dictionary import LMDictionary
from .scorer import SentimentResult, score_sentiment


@dataclass(frozen=True)
class RiskFactorsSentimentResult:
    skipped: bool
    skip_reason: Optional[str]
    result: Optional[SentimentResult]


def score_mdna_sentiment(
    sections: FilingTextSections,
    dictionary: LMDictionary,
) -> SentimentResult:
    if sections.item_7_mdna is None:
        raise MissingSectionError(
            "filing has no extracted Item 7 / MD&A section."
        )
    return score_sentiment(sections.item_7_mdna, dictionary)


def score_risk_factors_sentiment(
    sections: FilingTextSections,
    dictionary: LMDictionary,
) -> RiskFactorsSentimentResult:
    if sections.item_1a_risk_factors is None:
        raise MissingSectionError(
            "filing has no extracted Item 1A Risk Factors section."
        )

    if sections.is_risk_factors_boilerplate:
        return RiskFactorsSentimentResult(
            skipped=True,
            skip_reason=(
                "filing's Item 1A is the 10-Q 'no material changes' "
                "boilerplate -- scoring it would measure the tone of a "
                "disclaimer sentence, not of the actual risk factors on "
                "file (those are still whatever the most recent 10-K said)."
            ),
            result=None,
        )

    return RiskFactorsSentimentResult(
        skipped=False,
        skip_reason=None,
        result=score_sentiment(sections.item_1a_risk_factors, dictionary),
    )
