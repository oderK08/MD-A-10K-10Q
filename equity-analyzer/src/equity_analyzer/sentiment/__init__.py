"""
Module 4 -- Sentiment: Loughran-McDonald word-list scoring for financial
text, with tone comparison across filings.

Requires the real Loughran-McDonald Master Dictionary CSV, downloaded
separately (see `lm_dictionary.load_lm_dictionary` for why we don't ship
a hardcoded copy) and loaded once per process/run.
"""

from .errors import DictionaryFormatError, EmptyTextError, MissingSectionError, SentimentError
from .lm_dictionary import CATEGORIES, LMDictionary, load_lm_dictionary
from .scorer import SentimentComparison, SentimentResult, compare_sentiment, score_sentiment
from .sections import RiskFactorsSentimentResult, score_mdna_sentiment, score_risk_factors_sentiment

__all__ = [
    "SentimentError",
    "DictionaryFormatError",
    "EmptyTextError",
    "MissingSectionError",
    "CATEGORIES",
    "LMDictionary",
    "load_lm_dictionary",
    "SentimentResult",
    "SentimentComparison",
    "score_sentiment",
    "compare_sentiment",
    "RiskFactorsSentimentResult",
    "score_mdna_sentiment",
    "score_risk_factors_sentiment",
]
