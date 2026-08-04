import pytest

from equity_analyzer.data_layer.models import FilingTextSections
from equity_analyzer.sentiment.errors import MissingSectionError
from equity_analyzer.sentiment.lm_dictionary import LMDictionary
from equity_analyzer.sentiment.sections import score_mdna_sentiment, score_risk_factors_sentiment

DICTIONARY = LMDictionary(
    words_by_category={
        "negative": frozenset({"decline"}),
        "positive": frozenset({"growth"}),
        "uncertainty": frozenset(),
        "litigious": frozenset(),
        "strong_modal": frozenset(),
        "weak_modal": frozenset(),
        "constraining": frozenset(),
    }
)

BOILERPLATE_TEXT = (
    "There have been no material changes to the risk factors disclosed "
    "in our Annual Report on Form 10-K."
)


def test_score_mdna_sentiment_raises_when_missing():
    sections = FilingTextSections(item_7_mdna=None)
    with pytest.raises(MissingSectionError, match="MD&A"):
        score_mdna_sentiment(sections, DICTIONARY)


def test_score_mdna_sentiment_scores_normally():
    sections = FilingTextSections(item_7_mdna="growth was strong this quarter")
    result = score_mdna_sentiment(sections, DICTIONARY)
    assert result.counts["positive"] == 1


def test_score_risk_factors_raises_when_missing():
    sections = FilingTextSections(item_1a_risk_factors=None)
    with pytest.raises(MissingSectionError, match="Risk Factors"):
        score_risk_factors_sentiment(sections, DICTIONARY)


def test_score_risk_factors_skips_boilerplate():
    sections = FilingTextSections(
        item_1a_risk_factors=BOILERPLATE_TEXT,
        is_risk_factors_boilerplate=True,
    )
    result = score_risk_factors_sentiment(sections, DICTIONARY)
    assert result.skipped is True
    assert result.result is None
    assert "boilerplate" in result.skip_reason.lower() or "disclaimer" in result.skip_reason.lower()


def test_score_risk_factors_scores_when_not_boilerplate():
    sections = FilingTextSections(
        item_1a_risk_factors="our revenue could decline due to competition",
        is_risk_factors_boilerplate=False,
    )
    result = score_risk_factors_sentiment(sections, DICTIONARY)
    assert result.skipped is False
    assert result.result is not None
    assert result.result.counts["negative"] == 1
