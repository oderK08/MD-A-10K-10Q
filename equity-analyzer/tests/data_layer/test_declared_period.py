"""
Tests for reading the period a company declares on its own call.

Why this matters more than it sounds: the report compares two
consecutive quarters. Get the mapping wrong and the tool diffs two
periods that do not belong together and presents the result with full
confidence -- no crash, no empty section, just an analysis of the wrong
pair.
"""

from equity_analyzer.data_layer.declared_period import read_declared_period

# The real Microsoft opening, which is what revealed that Alpha
# Vantage's `quarter` parameter is fiscal and not calendar.
MSFT_OPENING = (
    "Greetings, and welcome to the Microsoft Corporation Fiscal Year 2026 Second "
    "Quarter Earnings Conference Call. At this time, all participants are in a "
    "listen-only mode. A question and answer session will follow the formal presentation."
)


def test_microsoft_declares_a_fiscal_quarter_not_a_calendar_one():
    period = read_declared_period(MSFT_OPENING)
    assert period.label == "2026Q2"
    assert period.says_fiscal is True
    assert "Fiscal Year 2026 Second Quarter" in period.phrase


def test_the_common_phrasings_are_all_read():
    cases = {
        "welcome to the second quarter of fiscal 2026 earnings call": ("2026Q2", True),
        "welcome to the first quarter 2026 earnings conference call": ("2026Q1", False),
        "welcome to the Q3 fiscal 2027 earnings call": ("2027Q3", True),
        "welcome to the fourth quarter 2025 results call": ("2025Q4", False),
    }
    for text, (label, fiscal) in cases.items():
        period = read_declared_period(text)
        assert period is not None, text
        assert period.label == label, text
        assert period.says_fiscal is fiscal, text


def test_only_the_opening_is_searched():
    """
    Later in a call, speakers reference prior-year comparatives and
    next-quarter guidance constantly, and any of those would match. The
    current period is always named in the greeting.
    """
    text = (
        "Welcome to the third quarter 2026 earnings call. "
        + "Filler. " * 300
        + "In the first quarter of 2024 we saw very different conditions."
    )
    period = read_declared_period(text)
    assert period.label == "2026Q3"


def test_a_call_that_names_no_period_returns_none_rather_than_guessing():
    assert read_declared_period("Welcome, everyone. Thanks for joining us today.") is None


def test_a_calendar_filer_and_a_fiscal_filer_are_distinguishable():
    """
    The point of the whole module: whether the label needs translating
    depends on this flag, and it must come from the company's wording,
    never from an assumption about the ticker.
    """
    calendar = read_declared_period("welcome to the second quarter 2026 earnings call")
    fiscal = read_declared_period("welcome to the fiscal year 2026 second quarter call")
    assert calendar.label == fiscal.label == "2026Q2"
    assert calendar.says_fiscal is False
    assert fiscal.says_fiscal is True
