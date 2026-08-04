"""
Loads the Loughran-McDonald Master Dictionary from its official CSV export.

This repo does NOT ship the actual dictionary. It's a maintained, versioned
academic word list (tens of thousands of words, revised periodically),
published free of charge by the University of Notre Dame's Software
Repository for Accounting and Finance:

    https://sraf.nd.edu/loughranmcdonald-master-dictionary/

We deliberately do not hardcode a partial, from-memory reproduction of it.
An incomplete or stale copy would silently under-count sentiment
categories with no way for anyone downstream to notice -- exactly the
kind of quiet-wrongness this project exists to avoid. Download the
current CSV from that page and pass its path to `load_lm_dictionary`.

Expected columns (matching the real file's header, case-insensitive,
spaces/underscores interchangeable): Word, Negative, Positive,
Uncertainty, Litigious, Strong_Modal, Weak_Modal, Constraining. In the
real file, a word's value in one of these columns is 0 if it doesn't
belong to that category, or the four-digit year it was added if it does
-- this loader only cares whether the cell is zero or not. Extra columns
present in the real file (Word Count, Doc Count, Syllables, Source, ...)
are ignored, not required.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from .errors import DictionaryFormatError

CATEGORIES = (
    "negative",
    "positive",
    "uncertainty",
    "litigious",
    "strong_modal",
    "weak_modal",
    "constraining",
)

# Column-name aliases actually seen across different releases of the
# published file (naming has varied slightly over the years).
_CATEGORY_ALIASES = {
    "negative": ["negative"],
    "positive": ["positive"],
    "uncertainty": ["uncertainty"],
    "litigious": ["litigious"],
    "strong_modal": ["strong_modal", "strongmodal"],
    "weak_modal": ["weak_modal", "weakmodal"],
    "constraining": ["constraining"],
}
_WORD_COLUMN_ALIASES = ["word"]


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


@dataclass(frozen=True)
class LMDictionary:
    """Lowercased word sets per Loughran-McDonald category."""
    words_by_category: dict = field(default_factory=dict)  # category -> frozenset[str]


def load_lm_dictionary(csv_path: Union[str, Path]) -> LMDictionary:
    """
    Parses the official Loughran-McDonald Master Dictionary CSV into an
    LMDictionary. Raises DictionaryFormatError if a required column can't
    be found, rather than silently scoring against an incomplete set of
    categories.
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise DictionaryFormatError(f"'{csv_path}' has no header row.")

        normalized_to_actual = {_normalize_header(name): name for name in reader.fieldnames}

        word_column = next(
            (normalized_to_actual[alias] for alias in _WORD_COLUMN_ALIASES
             if alias in normalized_to_actual),
            None,
        )
        if word_column is None:
            raise DictionaryFormatError(
                f"'{csv_path}' has no 'Word' column (found: {reader.fieldnames})."
            )

        category_columns: dict = {}
        for category, aliases in _CATEGORY_ALIASES.items():
            match = next((normalized_to_actual[a] for a in aliases if a in normalized_to_actual), None)
            if match is None:
                raise DictionaryFormatError(
                    f"'{csv_path}' is missing a column for category "
                    f"{category!r} (expected one of {aliases}, found: "
                    f"{reader.fieldnames}). This doesn't look like a real "
                    f"Loughran-McDonald Master Dictionary export."
                )
            category_columns[category] = match

        words_by_category: dict = {category: set() for category in CATEGORIES}
        for row in reader:
            word = (row.get(word_column) or "").strip().lower()
            if not word:
                continue
            for category, column in category_columns.items():
                raw = (row.get(column) or "0").strip()
                if raw and raw != "0":
                    words_by_category[category].add(word)

    return LMDictionary(
        words_by_category={c: frozenset(w) for c, w in words_by_category.items()}
    )
