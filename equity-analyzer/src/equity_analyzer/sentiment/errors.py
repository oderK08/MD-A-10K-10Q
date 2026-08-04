"""Errors for the sentiment module."""

from __future__ import annotations


class SentimentError(Exception):
    """Base class for all sentiment-scoring errors."""


class DictionaryFormatError(SentimentError):
    """The Loughran-McDonald CSV is missing an expected column."""


class EmptyTextError(SentimentError):
    """There are no alphabetic words to score in the given text."""


class MissingSectionError(SentimentError):
    """A required section wasn't extracted from the filing."""
