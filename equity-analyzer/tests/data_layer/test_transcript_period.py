"""
Tests for deriving the transcript provider's quarter label.

A live request for MSFT 2026Q2 returned the call for the quarter that
ended in December 2025 -- the provider labels by the ISSUER's fiscal
calendar. Ask with a calendar label for a June-year filer and you get a
call two quarters from the one you meant, with no crash and no empty
section to give it away.
"""

import pytest

from equity_analyzer.data_layer.transcript_period import (
    PeriodLabelUnavailable,
    alpha_vantage_label,
    previous_label,
    verify_against_declared,
)


def test_the_label_comes_from_edgars_own_fiscal_markers():
    """
    EDGAR carries `fy`/`fp` -- the company's own fiscal year and period
    -- on every XBRL fact, and the pipeline already extracts them. Same
    basis the provider indexes by, so no fiscal-calendar table has to be
    hardcoded or maintained.
    """
    assert alpha_vantage_label(2026, "Q2") == "2026Q2"
    assert alpha_vantage_label(2027, "Q1") == "2027Q1"


def test_an_annual_filing_maps_to_the_fourth_quarter():
    """
    EDGAR labels a 10-K "FY". Companies present Q4 and full-year results
    on a single call, so that is the quarter to ask for.
    """
    assert alpha_vantage_label(2026, "FY") == "2026Q4"


def test_a_filing_without_fiscal_markers_raises_rather_than_assuming_calendar():
    """
    Falling back to the calendar quarter would be right for
    December-year filers and silently wrong for everyone else -- exactly
    the failure this module exists to prevent.
    """
    with pytest.raises(PeriodLabelUnavailable):
        alpha_vantage_label(None, "Q2")
    with pytest.raises(PeriodLabelUnavailable):
        alpha_vantage_label(2026, None)
    with pytest.raises(PeriodLabelUnavailable):
        alpha_vantage_label(2026, "H1")


def test_the_previous_quarter_rolls_the_year_back_at_q1():
    assert previous_label("2026Q3") == "2026Q2"
    assert previous_label("2026Q1") == "2025Q4"


def test_a_mismatch_between_requested_and_declared_is_reported():
    """
    Even with the label sourced from EDGAR, the provider may index
    differently. The company states the period out loud in the opening
    seconds, so the check is free -- and a wrong pairing is otherwise
    invisible in the output.
    """
    msft = (
        "Greetings, and welcome to the Microsoft Corporation Fiscal Year 2026 "
        "Second Quarter Earnings Conference Call."
    )
    assert verify_against_declared("2026Q2", msft) is None

    warning = verify_against_declared("2026Q4", msft)
    assert warning is not None
    assert "2026Q4" in warning and "2026Q2" in warning


def test_a_call_that_names_no_period_is_flagged_not_passed_silently():
    warning = verify_against_declared("2026Q2", "Welcome everyone, thanks for joining.")
    assert warning is not None
    assert "non déclarée" in warning
