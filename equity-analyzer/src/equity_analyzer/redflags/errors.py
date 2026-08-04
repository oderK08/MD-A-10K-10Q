"""Errors for the red-flags module.

Kept as explicit, named exceptions (rather than bare ValueError) so callers
can distinguish "the input data isn't safe to compare" from "a required
number is missing" -- an advisory tool should fail loudly and specifically,
never fall back to a guessed number.
"""

from __future__ import annotations


class RedFlagError(Exception):
    """Base class for all red-flag calculation errors."""


class InsufficientDataError(RedFlagError):
    """A FinancialPeriod is missing a metric required for this score."""


class IncomparablePeriodsError(RedFlagError):
    """
    Two FinancialPeriods were passed for a YoY comparison but aren't safely
    comparable (mismatched duration, or mismatched fiscal quarter, or not
    actually a year-over-year pair).
    """
