"""
Assembles the outputs of Modules 1-4 into one ReportData object, ready to
render.

The rest of the pipeline is deliberately strict: a red-flag calculator
raises rather than guess at a missing metric, a diff refuses to score
10-Q boilerplate, a sentiment scorer refuses empty text. That's correct
for each module in isolation. This orchestration layer is different on
purpose: an advisory report covering four analyses shouldn't fail
entirely because ONE of them (say, Beneish M, needing a prior period
nobody supplied) isn't computable. Every optional section is wrapped in
`SectionResult`, which is either the computed value or a plain-English
reason it isn't available -- so the report can say "Beneish M-Score:
unavailable -- no prior-period filing provided" right next to the
sections that DID compute, instead of one gap taking down the whole
report or, worse, silently showing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, TypeVar

from ..data_layer.models import Filing, FinancialPeriod
from ..diff.errors import DiffError
from ..diff.mdna import diff_mdna
from ..diff.risk_factors import RiskFactorsDiffResult, diff_risk_factors
from ..diff.text_diff import TextDiffResult
from ..redflags.altman_z import AltmanZResult, altman_z_score
from ..redflags.beneish_m import BeneishMResult, beneish_m_score
from ..redflags.errors import RedFlagError
from ..redflags.piotroski_f import PiotroskiFResult, piotroski_f_score
from ..sentiment.errors import SentimentError
from ..sentiment.lm_dictionary import LMDictionary
from ..sentiment.scorer import SentimentResult
from ..sentiment.sections import (
    RiskFactorsSentimentResult,
    score_mdna_sentiment,
    score_risk_factors_sentiment,
)

T = TypeVar("T")

FINANCIAL_HIGHLIGHT_FIELDS = [
    ("revenue", "Revenue"),
    ("net_income", "Net Income"),
    ("operating_income", "Operating Income"),
    ("total_assets", "Total Assets"),
    ("total_liabilities", "Total Liabilities"),
    ("total_equity", "Total Equity"),
    ("operating_cash_flow", "Operating Cash Flow"),
]


@dataclass(frozen=True)
class SectionResult:
    """Either a computed value, or a plain-English reason it isn't available."""
    value: object
    unavailable_reason: Optional[str]

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


@dataclass(frozen=True)
class FinancialHighlight:
    label: str
    value: Optional[float]
    concept: Optional[str]  # which XBRL tag resolved this value, for auditability


@dataclass(frozen=True)
class ReportData:
    filing: Filing
    prior_filing: Optional[Filing]
    generated_at: datetime
    financial_highlights: list = field(default_factory=list)  # list[FinancialHighlight]
    altman_z: SectionResult = None
    beneish_m: SectionResult = None
    piotroski_f: SectionResult = None
    mdna_diff: SectionResult = None
    risk_factors_diff: SectionResult = None
    mdna_sentiment: SectionResult = None
    risk_factors_sentiment: SectionResult = None


def _ok(value: T) -> SectionResult:
    return SectionResult(value=value, unavailable_reason=None)


def _unavailable(reason: str) -> SectionResult:
    return SectionResult(value=None, unavailable_reason=reason)


def _attempt(compute: Callable[[], T]) -> SectionResult:
    """
    Runs `compute`, catching exactly the domain errors each module raises
    on purpose (missing metric, incomparable periods, empty text, missing
    section) and turning them into an `unavailable` section. Anything
    else (a real bug -- AttributeError, TypeError, ...) is NOT caught
    here and propagates, so a programming mistake doesn't masquerade as
    "data unavailable".
    """
    try:
        return _ok(compute())
    except (RedFlagError, DiffError, SentimentError) as exc:
        return _unavailable(str(exc))


def _financial_highlights(financials: Optional[FinancialPeriod]) -> list:
    if financials is None:
        return []
    highlights = []
    for field_name, label in FINANCIAL_HIGHLIGHT_FIELDS:
        fact = getattr(financials, field_name)
        highlights.append(
            FinancialHighlight(
                label=label,
                value=fact.value if fact is not None else None,
                concept=fact.concept if fact is not None else None,
            )
        )
    return highlights


def build_report_data(
    filing: Filing,
    prior_filing: Optional[Filing] = None,
    lm_dictionary: Optional[LMDictionary] = None,
    market_value_of_equity: Optional[float] = None,
    beneish_threshold: Optional[float] = None,
    generated_at: Optional[datetime] = None,
) -> ReportData:
    """
    Builds a full ReportData for `filing`, optionally comparing it against
    `prior_filing` (required for Beneish M, Piotroski F, and both text
    diffs -- all of which are inherently year-over-year) and scoring
    sentiment with `lm_dictionary` (required for both sentiment sections).
    Any of these being None, or a downstream module refusing to compute
    for a specific reason (see module docstring), shows up as an
    `unavailable` section rather than failing report generation outright.
    """
    beneish_kwargs = {} if beneish_threshold is None else {"threshold": beneish_threshold}

    # -- Red flags (Module 2) --
    if filing.financials is None:
        no_financials = "no financial data extracted for this filing"
        altman_z = _unavailable(no_financials)
        beneish_m = _unavailable(no_financials)
        piotroski_f = _unavailable(no_financials)
    else:
        altman_z = _attempt(
            lambda: altman_z_score(filing.financials, market_value_of_equity=market_value_of_equity)
        )
        if prior_filing is None or prior_filing.financials is None:
            no_prior = "no prior-period financial data available for year-over-year comparison"
            beneish_m = _unavailable(no_prior)
            piotroski_f = _unavailable(no_prior)
        else:
            beneish_m = _attempt(
                lambda: beneish_m_score(filing.financials, prior_filing.financials, **beneish_kwargs)
            )
            piotroski_f = _attempt(
                lambda: piotroski_f_score(filing.financials, prior_filing.financials)
            )

    # -- Text diff (Module 3) --
    if filing.text_sections is None:
        no_sections = "no text sections extracted for this filing"
        mdna_diff = _unavailable(no_sections)
        risk_factors_diff = _unavailable(no_sections)
    elif prior_filing is None or prior_filing.text_sections is None:
        no_prior_sections = "no prior-period filing provided for comparison"
        mdna_diff = _unavailable(no_prior_sections)
        risk_factors_diff = _unavailable(no_prior_sections)
    else:
        mdna_diff = _attempt(
            lambda: diff_mdna(filing.text_sections, prior_filing.text_sections)
        )
        risk_factors_diff = _attempt(
            lambda: diff_risk_factors(filing.text_sections, prior_filing.text_sections)
        )

    # -- Sentiment (Module 4) --
    if filing.text_sections is None:
        mdna_sentiment = _unavailable("no text sections extracted for this filing")
        risk_factors_sentiment = _unavailable("no text sections extracted for this filing")
    elif lm_dictionary is None:
        no_dict = "no Loughran-McDonald dictionary provided"
        mdna_sentiment = _unavailable(no_dict)
        risk_factors_sentiment = _unavailable(no_dict)
    else:
        mdna_sentiment = _attempt(
            lambda: score_mdna_sentiment(filing.text_sections, lm_dictionary)
        )
        risk_factors_sentiment = _attempt(
            lambda: score_risk_factors_sentiment(filing.text_sections, lm_dictionary)
        )

    return ReportData(
        filing=filing,
        prior_filing=prior_filing,
        generated_at=generated_at or datetime.now(timezone.utc),
        financial_highlights=_financial_highlights(filing.financials),
        altman_z=altman_z,
        beneish_m=beneish_m,
        piotroski_f=piotroski_f,
        mdna_diff=mdna_diff,
        risk_factors_diff=risk_factors_diff,
        mdna_sentiment=mdna_sentiment,
        risk_factors_sentiment=risk_factors_sentiment,
    )
