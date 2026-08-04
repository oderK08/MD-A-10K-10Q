import pytest

from equity_analyzer.sentiment.errors import EmptyTextError
from equity_analyzer.sentiment.lm_dictionary import LMDictionary
from equity_analyzer.sentiment.scorer import compare_sentiment, score_sentiment

DICTIONARY = LMDictionary(
    words_by_category={
        "negative": frozenset({"loss", "decline"}),
        "positive": frozenset({"growth", "strong"}),
        "uncertainty": frozenset({"uncertain"}),
        "litigious": frozenset({"lawsuit"}),
        "strong_modal": frozenset({"must"}),
        "weak_modal": frozenset({"might"}),
        "constraining": frozenset({"restrict"}),
    }
)


def test_counts_words_case_insensitively():
    text = "Loss and decline hurt results. LOSS was significant."
    result = score_sentiment(text, DICTIONARY)
    assert result.counts["negative"] == 3  # Loss, decline, LOSS
    assert result.total_word_count == 8


def test_proportions_are_counts_over_total_words():
    text = "growth growth loss neutral neutral neutral neutral neutral"
    result = score_sentiment(text, DICTIONARY)
    assert result.total_word_count == 8
    assert result.proportions["positive"] == pytest.approx(2 / 8)
    assert result.proportions["negative"] == pytest.approx(1 / 8)


def test_net_tone_positive_when_more_positive_words():
    text = "growth growth strong loss"
    result = score_sentiment(text, DICTIONARY)
    # positive=3 (growth, growth, strong), negative=1
    assert result.net_tone == pytest.approx((3 - 1) / (3 + 1))


def test_net_tone_zero_when_no_positive_or_negative_words():
    text = "the company operates in many markets globally"
    result = score_sentiment(text, DICTIONARY)
    assert result.counts["positive"] == 0
    assert result.counts["negative"] == 0
    assert result.net_tone == 0.0


def test_categories_with_no_matches_are_zero_not_missing():
    text = "the company operates in many markets globally"
    result = score_sentiment(text, DICTIONARY)
    for category in ("negative", "positive", "uncertainty", "litigious",
                      "strong_modal", "weak_modal", "constraining"):
        assert category in result.counts
        assert result.counts[category] == 0


def test_numbers_are_excluded_from_word_count():
    text = "revenue was 1200000 dollars in growth"
    result = score_sentiment(text, DICTIONARY)
    # "1200000" is not alphabetic, so it doesn't count as a word
    assert result.total_word_count == 5  # revenue, was, dollars, in, growth


def test_empty_text_raises_empty_text_error():
    with pytest.raises(EmptyTextError):
        score_sentiment("1234 5678 !!!", DICTIONARY)


def test_compare_sentiment_tone_shift():
    prior = score_sentiment("growth growth loss neutral", DICTIONARY)
    current = score_sentiment("loss loss loss decline neutral", DICTIONARY)

    comparison = compare_sentiment(current, prior)
    assert comparison.tone_shift == pytest.approx(current.net_tone - prior.net_tone)
    assert comparison.tone_shift < 0  # tone got more negative
    assert comparison.proportion_deltas["negative"] > 0
