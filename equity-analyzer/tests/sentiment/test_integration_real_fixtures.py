"""
Integration test: run the actual Module 1 extraction pipeline
(extract_sections) on the real fixture HTML, then score it through
Module 4 -- following the same lesson learned in Module 3's regression
suite: hand-written synthetic text can hide problems that only show up
against realistically-shaped extracted text.
"""

from __future__ import annotations

from pathlib import Path

from equity_analyzer.data_layer.text_sections import extract_sections
from equity_analyzer.sentiment.lm_dictionary import load_lm_dictionary
from equity_analyzer.sentiment.sections import score_mdna_sentiment, score_risk_factors_sentiment

FIXTURE_10K = Path(__file__).parent.parent / "fixtures" / "sample_10k.html"
FIXTURE_DICTIONARY = Path(__file__).parent.parent / "fixtures" / "sample_lm_dictionary.csv"


def test_scores_mdna_from_real_extracted_text():
    html = FIXTURE_10K.read_text()
    sections = extract_sections(html)
    dictionary = load_lm_dictionary(FIXTURE_DICTIONARY)

    result = score_mdna_sentiment(sections, dictionary)

    # The fixture's MD&A is line-wrapped across multiple sentences inside
    # a single <p> tag (see Module 3's regression tests) -- confirm the
    # scorer still tokenizes the whole thing rather than choking on the
    # embedded newlines.
    assert result.total_word_count > 20


def test_scores_risk_factors_from_real_extracted_text():
    html = FIXTURE_10K.read_text()
    sections = extract_sections(html)
    dictionary = load_lm_dictionary(FIXTURE_DICTIONARY)

    assert sections.is_risk_factors_boilerplate is False
    result = score_risk_factors_sentiment(sections, dictionary)

    assert result.skipped is False
    assert result.result.total_word_count > 20
