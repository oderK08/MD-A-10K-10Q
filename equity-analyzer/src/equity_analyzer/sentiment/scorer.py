"""
Loughran-McDonald sentiment scoring: counts words in each LM category and
derives a net tone score, following the standard bag-of-words methodology
from Loughran & McDonald (2011) -- the same baseline used by most
practitioner implementations of this dictionary.

Known simplifications, made explicit rather than silently assumed:
- Plain word-count matching, no negation handling (e.g. "not profitable"
  still counts "profitable" as positive). This matches the original
  paper's baseline methodology; negation-aware scoring is a documented
  possible future refinement, not a correctness bug in this version.
- Tokenization keeps alphabetic runs only (`[a-zA-Z]+`), matching the
  standard LM replication approach of excluding numbers from the word
  count denominator (financial filings are numbers-heavy; including them
  would dilute every proportion without adding sentiment signal). This
  means an abbreviation like "U.S." tokenizes as "U" and "S" rather than
  one token -- a minor, harmless approximation (neither fragment matches
  any LM category).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .errors import EmptyTextError
from .lm_dictionary import CATEGORIES, LMDictionary

_WORD_RE = re.compile(r"[a-zA-Z]+")


@dataclass(frozen=True)
class SentimentResult:
    total_word_count: int
    counts: dict          # category -> raw count
    proportions: dict     # category -> count / total_word_count
    net_tone: float        # (positive - negative) / (positive + negative); 0.0 if both are 0


@dataclass(frozen=True)
class SentimentComparison:
    tone_shift: float             # current.net_tone - prior.net_tone
    proportion_deltas: dict        # category -> current.proportions[c] - prior.proportions[c]


def score_sentiment(text: str, dictionary: LMDictionary) -> SentimentResult:
    """
    Scores `text` against every category in `dictionary`. Raises
    EmptyTextError if there are no alphabetic words at all -- a score of
    all zeros would look like a real (neutral) result rather than "there
    was nothing to analyze".
    """
    words = [w.lower() for w in _WORD_RE.findall(text)]
    total = len(words)
    if total == 0:
        raise EmptyTextError("no alphabetic words found in the given text.")

    word_counts = Counter(words)
    counts = {
        category: sum(n for word, n in word_counts.items() if word in wordset)
        for category, wordset in dictionary.words_by_category.items()
    }
    # Make sure every known category is present even if the dictionary
    # (e.g. a partial test fixture) didn't define it.
    for category in CATEGORIES:
        counts.setdefault(category, 0)

    proportions = {category: counts[category] / total for category in counts}

    positive = counts.get("positive", 0)
    negative = counts.get("negative", 0)
    net_tone = (positive - negative) / (positive + negative) if (positive + negative) else 0.0

    return SentimentResult(
        total_word_count=total,
        counts=counts,
        proportions=proportions,
        net_tone=net_tone,
    )


def compare_sentiment(current: SentimentResult, prior: SentimentResult) -> SentimentComparison:
    """
    Tone shift between two already-scored texts. Not restricted to a
    particular pair of period types (unlike Module 2's quantitative red
    flags) since proportions are already normalized for document length --
    but for the cleanest read, compare the same kind of section across
    consecutive filings of the same form type (10-Q vs 10-Q, or 10-K vs
    10-K), so that a shift in tone is not really a shift in document type.
    """
    categories = set(current.proportions) | set(prior.proportions)
    proportion_deltas = {
        category: current.proportions.get(category, 0.0) - prior.proportions.get(category, 0.0)
        for category in categories
    }
    return SentimentComparison(
        tone_shift=current.net_tone - prior.net_tone,
        proportion_deltas=proportion_deltas,
    )
