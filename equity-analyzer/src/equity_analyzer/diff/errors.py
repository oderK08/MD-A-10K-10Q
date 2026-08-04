"""Errors for the text-diff module."""

from __future__ import annotations


class DiffError(Exception):
    """Base class for all text-diff errors."""


class MissingSectionError(DiffError):
    """A required section wasn't extracted from one of the two filings."""
