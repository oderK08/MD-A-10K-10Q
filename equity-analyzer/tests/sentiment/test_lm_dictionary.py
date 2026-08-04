"""
Tests for the CSV loader, run against tests/fixtures/sample_lm_dictionary.csv
-- a small, HAND-WRITTEN fixture with ~15 words, NOT the real
Loughran-McDonald Master Dictionary (which has tens of thousands). It only
exists to validate the loader's parsing logic; real scoring requires the
actual file (see lm_dictionary.py's module docstring).
"""

from pathlib import Path

import pytest

from equity_analyzer.sentiment.errors import DictionaryFormatError
from equity_analyzer.sentiment.lm_dictionary import CATEGORIES, load_lm_dictionary

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sample_lm_dictionary.csv"


def test_loads_all_categories():
    dictionary = load_lm_dictionary(FIXTURE_PATH)
    assert set(dictionary.words_by_category) == set(CATEGORIES)


def test_words_are_lowercased_and_correctly_categorized():
    dictionary = load_lm_dictionary(FIXTURE_PATH)
    assert "loss" in dictionary.words_by_category["negative"]
    assert "growth" in dictionary.words_by_category["positive"]
    assert "uncertain" in dictionary.words_by_category["uncertainty"]
    assert "litigation" in dictionary.words_by_category["litigious"]
    assert "must" in dictionary.words_by_category["strong_modal"]
    assert "might" in dictionary.words_by_category["weak_modal"]
    assert "restrict" in dictionary.words_by_category["constraining"]


def test_word_not_in_a_category_is_absent_not_present_as_false():
    dictionary = load_lm_dictionary(FIXTURE_PATH)
    assert "loss" not in dictionary.words_by_category["positive"]
    assert "growth" not in dictionary.words_by_category["negative"]


def test_missing_category_column_raises_dictionary_format_error(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("Word,Negative,Positive\nLOSS,2009,0\n")
    with pytest.raises(DictionaryFormatError, match="uncertainty"):
        load_lm_dictionary(bad_csv)


def test_missing_word_column_raises_dictionary_format_error(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text(
        "Term,Negative,Positive,Uncertainty,Litigious,Strong_Modal,Weak_Modal,Constraining\n"
        "LOSS,2009,0,0,0,0,0,0\n"
    )
    with pytest.raises(DictionaryFormatError, match="Word"):
        load_lm_dictionary(bad_csv)


def test_extra_unrelated_columns_are_ignored(tmp_path):
    """The real file has extra columns (Word Count, Doc Count, Syllables...);
    the loader must not require them."""
    csv_with_extra = tmp_path / "extra.csv"
    csv_with_extra.write_text(
        "Word,Seq_num,Negative,Positive,Uncertainty,Litigious,Strong_Modal,"
        "Weak_Modal,Constraining,Syllables\n"
        "LOSS,1,2009,0,0,0,0,0,0,1\n"
    )
    dictionary = load_lm_dictionary(csv_with_extra)
    assert "loss" in dictionary.words_by_category["negative"]
