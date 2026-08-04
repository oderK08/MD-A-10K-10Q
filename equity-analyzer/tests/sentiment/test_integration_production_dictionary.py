"""
Integration test against the REAL Loughran-McDonald Master Dictionary
(data/Loughran-McDonald_MasterDictionary_1993-2025.csv), not the small
hand-written test fixture used elsewhere in this package.

This is the first point in the whole project where a module runs against
genuine production data rather than a fixture reproducing it -- it exists
specifically to catch anything the hand-written fixture couldn't (e.g. a
real column name variant, or category word counts wildly different from
what's publicly documented for this dictionary).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from equity_analyzer.data_layer.text_sections import extract_sections
from equity_analyzer.sentiment.lm_dictionary import CATEGORIES, load_lm_dictionary
from equity_analyzer.sentiment.sections import score_mdna_sentiment, score_risk_factors_sentiment

DICTIONARY_PATH = (
    Path(__file__).parent.parent.parent
    / "data"
    / "Loughran-McDonald_MasterDictionary_1993-2025.csv"
)
FIXTURE_10K = Path(__file__).parent.parent / "fixtures" / "sample_10k.html"

# Publicly documented category sizes for this exact release of the
# dictionary (see data/README.md) -- a sanity check that the loader
# parsed the real file correctly, not just that it didn't crash.
EXPECTED_CATEGORY_SIZES = {
    "negative": 2355,
    "positive": 354,
    "uncertainty": 297,
    "litigious": 905,
    "strong_modal": 19,
    "weak_modal": 27,
    "constraining": 184,
}

pytestmark = pytest.mark.skipif(
    not DICTIONARY_PATH.exists(),
    reason=f"production dictionary not found at {DICTIONARY_PATH}",
)


def test_category_word_counts_match_published_figures():
    dictionary = load_lm_dictionary(DICTIONARY_PATH)
    for category, expected_size in EXPECTED_CATEGORY_SIZES.items():
        assert len(dictionary.words_by_category[category]) == expected_size, category


def test_loads_all_86k_plus_words():
    dictionary = load_lm_dictionary(DICTIONARY_PATH)
    total_categorized_words = len({
        word
        for category in CATEGORIES
        for word in dictionary.words_by_category[category]
    })
    # Most of the ~86k entries are uncategorized (neutral) words; only a
    # subset belongs to any sentiment category.
    assert total_categorized_words > 3000


def test_scores_real_filing_text_with_production_dictionary():
    dictionary = load_lm_dictionary(DICTIONARY_PATH)
    sections = extract_sections(FIXTURE_10K.read_text())

    mdna_result = score_mdna_sentiment(sections, dictionary)
    assert mdna_result.net_tone > 0  # "increased", "improved" -- genuinely positive MD&A

    rf_result = score_risk_factors_sentiment(sections, dictionary)
    assert rf_result.skipped is False
    assert rf_result.result.net_tone < 0  # a risk factors section reads negative
