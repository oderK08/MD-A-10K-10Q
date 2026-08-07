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

from ..data_layer.edgar_client import filing_index_url
from ..data_layer.models import Filing, FinancialPeriod
from ..data_layer.xbrl_normalizer import CANDIDATE_TAGS
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
from .risk_alert import evaluate_risk_alert

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
    financial_data_completeness: Optional[float] = None  # resolved / expected metrics, 0..1
    source_filing_url: Optional[str] = None  # traceability link to the real SEC filing
    # The annual filings the red flags were computed from, when those are
    # NOT `filing`/`prior_filing` -- i.e. whenever the report is about a
    # quarter. Altman, Beneish and Piotroski are annual models (see
    # build_report_data), so a quarterly report reads its text from the
    # 10-Q pair and its scores from the 10-K pair, and has to be able to
    # tell the reader which year those scores are for.
    annual_filing: Optional[Filing] = None
    prior_annual_filing: Optional[Filing] = None
    altman_z: SectionResult = None
    beneish_m: SectionResult = None
    piotroski_f: SectionResult = None
    mdna_diff: SectionResult = None
    risk_factors_diff: SectionResult = None
    mdna_sentiment: SectionResult = None
    risk_factors_sentiment: SectionResult = None
    # Quarterly only: did this 10-Q write real risk-factor text instead
    # of the "no material changes" clause (see report/risk_alert.py).
    # None on an annual report, where the question doesn't apply.
    risk_alert: Optional[object] = None  # Optional[RiskFactorsAlert]
    # Both opt-in, both set by an explicit caller AFTER the free,
    # deterministic report below is already built -- never by
    # build_report_data() itself. See report/theme_selection.py and
    # report/ai_summary.py.
    #   theme_selection: which sub-themes an analyst would watch, and
    #     how that call was made (AI or a documented fallback).
    #   ai_summary: the bullish/bearish read written over that selection.
    theme_selection: SectionResult = None
    ai_summary: SectionResult = None


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


def _data_completeness(financials: Optional[FinancialPeriod]) -> Optional[float]:
    """
    Fraction of the metrics we look for (CANDIDATE_TAGS) that actually
    resolved to a value for this period -- a plain, honest signal of how
    complete the underlying data is, shown directly in the report rather
    than making a reader infer it by counting "unavailable" red-flag
    sections themselves.
    """
    if financials is None:
        return None
    return len(financials.resolved_tags) / len(CANDIDATE_TAGS)


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


def _is_annual(filing: Optional[Filing]) -> bool:
    if filing is None:
        return False
    form = getattr(filing.form_type, "value", filing.form_type)
    return form == "10-K"


def build_report_data(
    filing: Filing,
    prior_filing: Optional[Filing] = None,
    lm_dictionary: Optional[LMDictionary] = None,
    market_value_of_equity: Optional[float] = None,
    beneish_threshold: Optional[float] = None,
    generated_at: Optional[datetime] = None,
    annual_filing: Optional[Filing] = None,
    prior_annual_filing: Optional[Filing] = None,
) -> ReportData:
    """
    Builds a full ReportData for `filing`, optionally comparing it against
    `prior_filing` (required for both text diffs) and scoring sentiment
    with `lm_dictionary` (required for both sentiment sections). Any of
    these being None, or a downstream module refusing to compute for a
    specific reason (see module docstring), shows up as an `unavailable`
    section rather than failing report generation outright.

    THE RED FLAGS DO NOT COME FROM `filing` WHEN `filing` IS A QUARTER.
    Altman Z, Beneish M and Piotroski F are annual models: every one of
    them was estimated on full-year statements, and several of their
    inputs are meaningless on a quarter. Beneish's accruals and sales-
    growth indices compare a year to the year before; Piotroski's nine
    criteria are year-over-year tests; Altman's coefficients were fitted
    to annual balance sheets. Feeding them a 10-Q produces a number that
    is not a bad estimate but a category error, and it would look exactly
    like a good one.

    So a quarterly report passes its company's two most recent 10-Ks as
    `annual_filing` / `prior_annual_filing`, and the scores are computed
    from those -- correctly labelled with the year they belong to. A
    quarterly report with no annual filings supplied gets the scores
    marked unavailable, with that reason spelled out, rather than a
    quietly wrong number.
    """
    beneish_kwargs = {} if beneish_threshold is None else {"threshold": beneish_threshold}

    # -- Red flags (Module 2), always on annual data -- see the docstring --
    if annual_filing is not None:
        score_filing, score_prior = annual_filing, prior_annual_filing
    elif _is_annual(filing):
        score_filing, score_prior = filing, prior_filing
    else:
        score_filing, score_prior = None, None

    if score_filing is None:
        not_annual = (
            "no annual (10-K) filing supplied -- Altman Z, Beneish M and "
            "Piotroski F are annual models and are not computed on quarterly data"
        )
        altman_z = _unavailable(not_annual)
        beneish_m = _unavailable(not_annual)
        piotroski_f = _unavailable(not_annual)
    elif score_filing.financials is None:
        no_financials = "no financial data extracted for this filing"
        altman_z = _unavailable(no_financials)
        beneish_m = _unavailable(no_financials)
        piotroski_f = _unavailable(no_financials)
    else:
        altman_z = _attempt(
            lambda: altman_z_score(
                score_filing.financials, market_value_of_equity=market_value_of_equity
            )
        )
        if score_prior is None or score_prior.financials is None:
            no_prior = "no prior-period financial data available for year-over-year comparison"
            beneish_m = _unavailable(no_prior)
            piotroski_f = _unavailable(no_prior)
        else:
            beneish_m = _attempt(
                lambda: beneish_m_score(
                    score_filing.financials, score_prior.financials, **beneish_kwargs
                )
            )
            piotroski_f = _attempt(
                lambda: piotroski_f_score(score_filing.financials, score_prior.financials)
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
        financial_data_completeness=_data_completeness(filing.financials),
        source_filing_url=filing_index_url(filing.cik, filing.accession_number),
        annual_filing=annual_filing,
        prior_annual_filing=prior_annual_filing,
        risk_alert=evaluate_risk_alert(filing, risk_factors_diff, prior_filing),
        altman_z=altman_z,
        beneish_m=beneish_m,
        piotroski_f=piotroski_f,
        mdna_diff=mdna_diff,
        risk_factors_diff=risk_factors_diff,
        mdna_sentiment=mdna_sentiment,
        risk_factors_sentiment=risk_factors_sentiment,
    )
