"""
The provider throttle, tested with an injected clock so the suite never
actually waits.

This exists because of a real failure, not a hypothetical one. The
consensus request and the transcript request live in two different
modules, neither knew about the other, and a real run fired both in the
same millisecond. Alpha Vantage answered the second with "please
consider spreading out your free API requests more sparingly". The
consensus had already arrived, so the run looked like a company with no
transcript.
"""

from __future__ import annotations

import pytest

from equity_analyzer.data_layer import alpha_vantage
from equity_analyzer.data_layer.alpha_vantage import reset_throttle, soft_error, throttle


class _Clock:
    """A stopwatch that only moves when something sleeps on it."""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(alpha_vantage, "MIN_SECONDS_BETWEEN_REQUESTS", 15.0)
    reset_throttle()
    yield _Clock()
    reset_throttle()


def test_the_first_request_never_waits(clock):
    assert throttle(sleep=clock.sleep, now=clock.now) == 0.0
    assert clock.slept == []


def test_a_second_request_is_spaced_out(clock):
    """
    THE regression. Two callers, one provider: without this the report
    fires the consensus and the transcript in the same millisecond and
    the free tier refuses the second.
    """
    throttle(sleep=clock.sleep, now=clock.now)
    waited = throttle(sleep=clock.sleep, now=clock.now)

    assert waited == pytest.approx(15.0)
    assert clock.slept == [pytest.approx(15.0)]


def test_a_request_after_a_long_gap_does_not_wait(clock):
    """
    The gap is a floor, not a fixed cost. A run that spent thirty
    seconds fetching from EDGAR in between has already waited.
    """
    throttle(sleep=clock.sleep, now=clock.now)
    clock.t += 30.0

    assert throttle(sleep=clock.sleep, now=clock.now) == 0.0
    assert clock.slept == []


def test_three_requests_stay_spaced_from_each_other_not_from_the_first(clock):
    """
    Spacing is measured from the PREVIOUS request. Measured from the
    first, the third would not wait at all and would arrive back to back
    with the second.
    """
    for _ in range(3):
        throttle(sleep=clock.sleep, now=clock.now)

    assert clock.slept == [pytest.approx(15.0), pytest.approx(15.0)]


def test_reset_forgets_the_last_request(clock):
    throttle(sleep=clock.sleep, now=clock.now)
    reset_throttle()

    assert throttle(sleep=clock.sleep, now=clock.now) == 0.0


# -- The refusal that arrives as HTTP 200 -------------------------------


def test_a_refusal_message_is_detected_under_every_key_the_vendor_uses():
    for key in ("Information", "Note", "Error Message"):
        assert soft_error({key: "quota"}) is not None


def test_real_data_is_not_mistaken_for_a_refusal():
    assert soft_error({"quarterlyEarnings": [{"reportedEPS": "1.2"}]}) is None
    assert soft_error({"Information": ""}) is None


def test_a_non_dict_payload_is_not_a_refusal():
    """
    Some endpoints answer with a list. That is data, not a refusal, and
    treating it as one would turn a working call into a quota error.
    """
    assert soft_error([{"transcript": "..."}]) is None


def test_the_refusal_carries_the_vendors_own_words():
    """
    The message is the only thing that tells a burst limit from an
    exhausted daily budget, so it has to reach the log rather than being
    replaced by a generic sentence.
    """
    message = soft_error({"Information": "spreading out your free API requests"})
    assert "spreading out" in message


# -- Are the two callers actually wired to it? --------------------------
#
# The tests above prove the throttle spaces requests. These prove it is
# reached, which is the part that was missing in production: the logic
# was fine, nobody called it.


def test_the_transcript_source_goes_through_the_throttle(monkeypatch):
    from equity_analyzer.data_layer import transcript_source
    from equity_analyzer.data_layer.transcript_source import alpha_vantage_source

    calls = []
    monkeypatch.setattr(alpha_vantage, "throttle", lambda *a, **k: calls.append("t"))
    monkeypatch.setattr(transcript_source, "throttle", lambda *a, **k: calls.append("t"))
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-test")

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"transcript": [{"speaker": "CEO", "content": "Revenue grew."}]}

    monkeypatch.setattr(transcript_source.requests, "get", lambda *a, **k: _Response())
    alpha_vantage_source().fetch("MSFT", "0000789019", quarter="2026Q3")

    assert calls, "la requête transcript doit passer par le throttle du fournisseur"


def test_the_consensus_request_goes_through_the_throttle(monkeypatch):
    from equity_analyzer.data_layer import earnings_expectations

    calls = []
    monkeypatch.setattr(earnings_expectations, "throttle", lambda *a, **k: calls.append("t"))
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-test")

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"quarterlyEarnings": [
                {"fiscalDateEnding": "2026-03-31", "estimatedEPS": "4.09",
                 "reportedEPS": "4.27", "surprisePercentage": "4.4"}
            ]}

    monkeypatch.setattr(earnings_expectations.requests, "get", lambda *a, **k: _Response())
    earnings_expectations.fetch_earnings_expectations("MSFT")

    assert calls, "la requête consensus doit passer par le throttle du fournisseur"
