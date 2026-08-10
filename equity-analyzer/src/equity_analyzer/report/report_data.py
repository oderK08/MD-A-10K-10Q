"""
Assembles everything the two-page report shows into one object, ready to
render. Pure: no network, no API key, no clock beyond the timestamp it
is handed. Everything it needs has already been fetched by the caller.

That purity is what makes the report testable offline, which matters
more here than in most projects: the environment this was written in
cannot reach SEC EDGAR or the transcript provider, so the entire test
suite runs on fixtures and the first real run happens in CI.

WHY EVERY OPTIONAL SECTION IS A `SectionResult` AND NOT `None`. The
modules underneath are deliberately strict: a red-flag calculator raises
rather than guess a missing metric, a sentiment scorer refuses empty
text. That is right for a calculation in isolation and wrong for a
report, which should not lose its red flags because tone could not be
scored. So each one is wrapped in either a computed value or a
plain-language reason it is missing, and the reason is PRINTED. A blank
cell and an uncomputable one look identical on paper, and a reader will
read the blank as "nothing to report".

The one thing that is NOT optional is the reading of the call. Page 1 is
the reading; a report whose page 1 degrades to an apology, set in the
same type as a real analysis, is worse than no report. The caller is
expected to fail before it gets here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Optional, TypeVar

from ..data_layer.models import Filing
from ..redflags.altman_z import altman_z_score
from ..redflags.beneish_m import beneish_m_score
from ..redflags.errors import RedFlagError
from ..redflags.piotroski_f import piotroski_f_score
from ..sentiment.errors import SentimentError
from ..sentiment.lm_dictionary import LMDictionary
from ..sentiment.scorer import score_sentiment
from ..sentiment.sections import score_mdna_sentiment

T = TypeVar("T")


@dataclass(frozen=True)
class SectionResult:
    """Either a computed value, or a plain-language reason it is not available."""

    value: object
    unavailable_reason: Optional[str]

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


@dataclass(frozen=True)
class CallInfo:
    """Provenance of the transcript the reading was written over."""

    quarter: str
    word_count: int
    source: str
    # How far back the search had to walk to find a published call. 0 is
    # the newest quarter on file. Shown rather than hidden: a
    # quarter-old transcript is still useful, a quarter-old transcript
    # believed to be current is not.
    quarters_back: int = 0
    call_date: Optional[date] = None
    # Set when the period the company NAMES in the opening of the call
    # is not the one that was requested. Printed on the report, because
    # a wrong pairing is otherwise invisible in the output.
    period_warning: Optional[str] = None


@dataclass(frozen=True)
class CallReport:
    """Everything the two-page report renders, and nothing it does not."""

    ticker: str
    company_name: str
    cik: str
    generated_at: datetime
    call: CallInfo
    analysis: object  # CallAnalysis -- page 1 in full

    # What the quarter was measured against (Alpha Vantage EARNINGS).
    expectations: SectionResult = None
    expectations_history: list = field(default_factory=list)

    # The filings the numbers come from. `quarter_filing` is the 10-Q
    # matching the call, kept for its MD&A and for the EDGAR link;
    # `annual_filing` / `prior_annual_filing` are the two 10-Ks the red
    # flags are computed on and are NEVER the quarter (see below).
    quarter_filing: Optional[Filing] = None
    annual_filing: Optional[Filing] = None
    prior_annual_filing: Optional[Filing] = None
    source_filing_url: Optional[str] = None

    altman_z: SectionResult = None
    beneish_m: SectionResult = None
    piotroski_f: SectionResult = None

    tone_prepared: SectionResult = None
    tone_qa: SectionResult = None
    tone_mdna: SectionResult = None


def _ok(value: T) -> SectionResult:
    return SectionResult(value=value, unavailable_reason=None)


def _unavailable(reason: str) -> SectionResult:
    return SectionResult(value=None, unavailable_reason=reason)


def _attempt(compute: Callable[[], T]) -> SectionResult:
    """
    Runs `compute`, catching exactly the domain errors the modules raise
    on purpose (missing metric, incomparable periods, empty text) and
    turning them into an unavailable section. Anything else -- an
    AttributeError, a TypeError, a real programming mistake -- is NOT
    caught, so a bug never gets to masquerade as missing data.
    """
    try:
        return _ok(compute())
    except (RedFlagError, SentimentError) as exc:
        return _unavailable(str(exc))


def _red_flags(annual: Optional[Filing], prior_annual: Optional[Filing]) -> tuple:
    """
    Altman, Beneish and Piotroski, computed on annual filings only.

    NOT AN OPTIMISATION, A CORRECTNESS RULE. All three are annual
    models: every one was estimated on full-year statements, and several
    of their inputs are meaningless on a quarter. Beneish's accruals and
    sales-growth indices compare a year to the year before, Piotroski's
    nine criteria are year-over-year tests, Altman's coefficients were
    fitted to annual balance sheets. Feeding them a 10-Q does not
    produce a noisier estimate, it produces a category error that looks
    exactly like a good number.

    So they are computed from the company's two most recent 10-Ks, and
    when those are not available the scores are marked unavailable with
    that reason rather than silently computed on the quarter.
    """
    if annual is None:
        reason = (
            "aucun 10-K disponible : Altman, Beneish et Piotroski sont des modèles "
            "annuels et ne sont jamais calculés sur un trimestre"
        )
        return _unavailable(reason), _unavailable(reason), _unavailable(reason)

    if annual.financials is None:
        reason = "aucune donnée financière extraite du 10-K"
        return _unavailable(reason), _unavailable(reason), _unavailable(reason)

    altman = _attempt(lambda: altman_z_score(annual.financials))

    if prior_annual is None or prior_annual.financials is None:
        no_prior = "pas d'exercice précédent exploitable pour la comparaison annuelle"
        return altman, _unavailable(no_prior), _unavailable(no_prior)

    beneish = _attempt(lambda: beneish_m_score(annual.financials, prior_annual.financials))
    piotroski = _attempt(lambda: piotroski_f_score(annual.financials, prior_annual.financials))
    return altman, beneish, piotroski


def _tone(text: Optional[str], dictionary: Optional[LMDictionary], absent: str) -> SectionResult:
    if dictionary is None:
        return _unavailable("dictionnaire Loughran-McDonald non fourni")
    if not (text or "").strip():
        return _unavailable(absent)
    return _attempt(lambda: score_sentiment(text, dictionary))


def build_call_report(
    ticker: str,
    company_name: str,
    cik: str,
    transcript,
    analysis,
    *,
    call_quarter: str,
    quarters_back: int = 0,
    period_warning: Optional[str] = None,
    expectation=None,
    expectations_reason: Optional[str] = None,
    expectations_history=(),
    quarter_filing: Optional[Filing] = None,
    annual_filing: Optional[Filing] = None,
    prior_annual_filing: Optional[Filing] = None,
    lm_dictionary: Optional[LMDictionary] = None,
    source_filing_url: Optional[str] = None,
    generated_at: Optional[datetime] = None,
) -> CallReport:
    """
    Builds the report object. Every argument is already-fetched data.

    THE TONE IS SCORED ON THREE TEXTS, NOT ONE, because they are three
    different acts. Prepared remarks are written, lawyered and rehearsed,
    so their tone is a decision management made. The Q&A is unscripted,
    so its tone is closer to something observed than something chosen,
    and the GAP between the two is the part worth looking at: a
    confident script followed by a hedging Q&A is a specific and common
    pattern. The 10-Q's MD&A is the same management writing for the
    record weeks later, which is the slowest and most cautious register
    of the three.

    `expectations_reason` carries WHY consensus figures are missing when
    they are, so the report can print it. Passing neither an expectation
    nor a reason is treated as "nobody asked for them", which is a
    different statement from "we asked and could not get them".
    """
    altman, beneish, piotroski = _red_flags(annual_filing, prior_annual_filing)

    if expectation is not None:
        expectations = _ok(expectation)
    else:
        expectations = _unavailable(
            expectations_reason or "consensus non demandé pour ce rapport"
        )

    tone_mdna = _unavailable("aucun dépôt périodique lu pour ce trimestre")
    if quarter_filing is not None and quarter_filing.text_sections is not None:
        if lm_dictionary is None:
            tone_mdna = _unavailable("dictionnaire Loughran-McDonald non fourni")
        else:
            tone_mdna = _attempt(
                lambda: score_mdna_sentiment(quarter_filing.text_sections, lm_dictionary)
            )

    return CallReport(
        ticker=ticker,
        company_name=company_name,
        cik=cik,
        generated_at=generated_at or datetime.now(timezone.utc),
        call=CallInfo(
            quarter=call_quarter,
            word_count=transcript.word_count,
            source=transcript.source,
            quarters_back=quarters_back,
            call_date=transcript.call_date,
            period_warning=period_warning,
        ),
        analysis=analysis,
        expectations=expectations,
        expectations_history=list(expectations_history),
        quarter_filing=quarter_filing,
        annual_filing=annual_filing,
        prior_annual_filing=prior_annual_filing,
        source_filing_url=source_filing_url,
        altman_z=altman,
        beneish_m=beneish,
        piotroski_f=piotroski,
        tone_prepared=_tone(
            transcript.prepared_remarks,
            lm_dictionary,
            "remarques préparées non isolées dans ce transcript",
        ),
        tone_qa=_tone(
            transcript.qa,
            lm_dictionary,
            "session de questions non isolée dans ce transcript",
        ),
        tone_mdna=tone_mdna,
    )


__all__ = [
    "CallInfo",
    "CallReport",
    "SectionResult",
    "build_call_report",
]
