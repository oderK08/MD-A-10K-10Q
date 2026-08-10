"""
The consensus figures the call is read against.

Everything here runs on a hand-built payload rather than a live call:
Alpha Vantage is unreachable from this environment, and the parsing is
where the failure modes actually are anyway.
"""

from __future__ import annotations

from datetime import date

import pytest

from equity_analyzer.data_layer.earnings_expectations import (
    ExpectationsRefused,
    ExpectationsUnavailable,
    fetch_earnings_expectations,
    parse_earnings_payload,
)


def _payload(*quarters):
    return {"symbol": "TEST", "quarterlyEarnings": list(quarters)}


def _quarter(ending, estimated="1.00", reported="1.10", pct="10.0", reported_date=None):
    entry = {
        "fiscalDateEnding": ending,
        "estimatedEPS": estimated,
        "reportedEPS": reported,
        "surprise": "0.10",
        "surprisePercentage": pct,
    }
    if reported_date:
        entry["reportedDate"] = reported_date
    return entry


def test_parses_a_normal_payload():
    expectations = parse_earnings_payload(
        "TEST", _payload(_quarter("2025-12-31", reported_date="2026-02-03"))
    )

    quarter = expectations.quarters[0]
    assert quarter.fiscal_date_ending == date(2025, 12, 31)
    assert quarter.reported_date == date(2026, 2, 3)
    assert quarter.estimated_eps == 1.00
    assert quarter.reported_eps == 1.10
    assert quarter.surprise_pct == 10.0
    assert quarter.beat is True


def test_quarters_are_sorted_newest_first_whatever_the_vendor_sent():
    """
    Everything downstream steps through this list BY INDEX -- `at()`
    walks back from an anchor, `history_before()` slices forward -- so
    the ordering is load-bearing and must not depend on a field the
    vendor is free to reorder.
    """
    expectations = parse_earnings_payload(
        "TEST",
        _payload(
            _quarter("2025-03-31"), _quarter("2025-12-31"), _quarter("2025-09-30"),
        ),
    )
    endings = [q.fiscal_date_ending for q in expectations.quarters]
    assert endings == sorted(endings, reverse=True)


def test_a_missing_estimate_is_none_and_never_zero():
    """
    Alpha Vantage writes the literal string "None" for a quarter it has
    no estimate for. Coerced to 0.0 it would read as a real consensus of
    zero cents, which is a meaningful number for a loss making company
    and would make every beat look enormous.
    """
    expectations = parse_earnings_payload(
        "TEST", _payload(_quarter("2025-12-31", estimated="None"))
    )
    quarter = expectations.quarters[0]
    assert quarter.estimated_eps is None
    assert quarter.beat is None
    assert quarter.verdict == "inconnu"


def test_a_small_surprise_reads_as_in_line_rather_than_a_beat():
    """
    Beating by half a percent is meeting expectations. Calling it a beat
    would put a word on page 1 that the number does not support.
    """
    expectations = parse_earnings_payload(
        "TEST", _payload(_quarter("2025-12-31", estimated="1.00", reported="1.005", pct="0.5"))
    )
    assert expectations.quarters[0].verdict == "en ligne"


def test_a_refusal_dressed_as_http_200_is_raised_as_a_refusal():
    """
    Alpha Vantage answers an exhausted quota and a premium-only endpoint
    with HTTP 200 and prose under "Information". Read as data it looks
    like a company with no earnings history.
    """
    with pytest.raises(ExpectationsRefused) as exc:
        parse_earnings_payload("TEST", {"Information": "premium endpoint"})
    assert "premium" in str(exc.value)


def test_an_empty_history_is_unavailable_not_an_empty_object():
    with pytest.raises(ExpectationsUnavailable):
        parse_earnings_payload("TEST", {"symbol": "TEST", "quarterlyEarnings": []})


def test_entries_with_no_usable_date_are_dropped():
    """
    The period end date is the key everything is matched on, so an entry
    without one can only ever be shown next to the wrong quarter.
    """
    expectations = parse_earnings_payload(
        "TEST",
        _payload({"estimatedEPS": "1.00", "reportedEPS": "1.10"}, _quarter("2025-12-31")),
    )
    assert len(expectations.quarters) == 1


def test_at_matches_the_quarter_by_period_end():
    expectations = parse_earnings_payload(
        "TEST", _payload(_quarter("2025-12-31"), _quarter("2025-09-30"))
    )
    assert expectations.at(date(2025, 12, 31)).fiscal_date_ending == date(2025, 12, 31)


def test_at_walks_back_when_the_call_read_is_older_than_the_newest_filing():
    """
    A company can file its 10-Q days after reporting, before the
    provider has published the call. The reading is then of an older
    quarter, and its consensus has to move with it: measuring a reading
    of Q1 against the expectations for Q2 is worse than having no
    consensus at all, because it looks grounded.
    """
    expectations = parse_earnings_payload(
        "TEST", _payload(_quarter("2025-12-31"), _quarter("2025-09-30"))
    )
    assert expectations.at(date(2025, 12, 31), quarters_back=1).fiscal_date_ending == date(2025, 9, 30)


def test_at_returns_none_rather_than_guessing_when_the_anchor_is_absent():
    expectations = parse_earnings_payload("TEST", _payload(_quarter("2025-12-31")))
    assert expectations.at(date(2024, 6, 30)) is None
    assert expectations.at(date(2025, 12, 31), quarters_back=5) is None


def test_history_before_returns_the_preceding_quarters_newest_first():
    expectations = parse_earnings_payload(
        "TEST",
        _payload(
            _quarter("2025-12-31"), _quarter("2025-09-30"),
            _quarter("2025-06-30"), _quarter("2025-03-31"),
        ),
    )
    current = expectations.at(date(2025, 12, 31))
    history = expectations.history_before(current, count=2)

    assert [q.fiscal_date_ending for q in history] == [date(2025, 9, 30), date(2025, 6, 30)]


def test_no_api_key_is_an_absence_not_a_crash(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    with pytest.raises(ExpectationsUnavailable) as exc:
        fetch_earnings_expectations("TEST")
    assert "ALPHAVANTAGE_API_KEY" in str(exc.value)


def test_a_negative_offset_reaches_a_quarter_newer_than_the_anchor():
    """
    The call read is not always older than the newest filing. A company
    can announce a quarter it has not filed yet, so the call is NEWER
    than anything on EDGAR. The list is newest first, so one quarter
    newer is one index lower.
    """
    expectations = parse_earnings_payload(
        "TEST", _payload(_quarter("2026-09-30"), _quarter("2026-06-30"))
    )
    newer = expectations.at(date(2026, 6, 30), quarters_back=-1)
    assert newer.fiscal_date_ending == date(2026, 9, 30)


def test_a_negative_offset_past_the_newest_line_returns_none():
    """
    Right answer, not a failure: if the provider has not published that
    quarter either, there is no consensus to show, and wrapping around
    to the oldest entry would put a two year old figure on page 1.
    """
    expectations = parse_earnings_payload("TEST", _payload(_quarter("2026-06-30")))
    assert expectations.at(date(2026, 6, 30), quarters_back=-1) is None


# -- Two numbers that are not measuring the same thing ------------------


def test_an_implausible_gap_is_marked_as_a_basis_mismatch():
    """
    Found on a real UBER run: consensus 0.71 against a reported 0.13, a
    miss of 82%, twice running, inside a record that also held a +351%
    beat. No company misses its consensus by four fifths twice in a row.
    That pattern is the provider's GAAP `reportedEPS` being measured
    against an analyst consensus struck on an adjusted basis.
    """
    expectations = parse_earnings_payload(
        "UBER", _payload(_quarter("2026-03-31", estimated="0.71", reported="0.13", pct="-81.7"))
    )
    quarter = expectations.quarters[0]

    assert quarter.comparable is False
    assert quarter.verdict == "bases differentes"


def test_an_ordinary_surprise_stays_a_verdict():
    """
    The guard has to leave the normal case alone, or every report loses
    the section that gives the reading its direction.
    """
    expectations = parse_earnings_payload(
        "TEST", _payload(_quarter("2026-03-31", estimated="1.00", reported="1.08", pct="8.0"))
    )
    quarter = expectations.quarters[0]

    assert quarter.comparable is True
    assert quarter.verdict == "au-dessus"


def test_a_spectacular_beat_is_doubted_the_same_way_as_a_spectacular_miss():
    """
    Symmetric on purpose. A +351% beat is exactly as implausible as an
    82% miss, and doubting only the bad news would bias every report
    upward.
    """
    expectations = parse_earnings_payload(
        "TEST", _payload(_quarter("2026-03-31", estimated="0.10", reported="0.45", pct="350.7"))
    )
    assert expectations.quarters[0].comparable is False


def test_the_figures_themselves_are_still_carried_when_the_gap_is_doubted():
    """
    The numbers are real and worth printing. It is their COMPARISON that
    is not, so nothing is dropped, only qualified.
    """
    expectations = parse_earnings_payload(
        "UBER", _payload(_quarter("2026-03-31", estimated="0.71", reported="0.13", pct="-81.7"))
    )
    quarter = expectations.quarters[0]

    assert quarter.estimated_eps == 0.71
    assert quarter.reported_eps == 0.13
