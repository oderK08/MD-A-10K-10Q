"""
Shared test setup.

THE PROVIDER THROTTLE IS DISABLED FOR THE SUITE. `data_layer.alpha_vantage`
spaces real requests fifteen seconds apart, which is correct against the
live free tier and absurd in a test run: it took the suite from three
seconds to nearly two minutes, all of it spent asleep, and a suite slow
enough to skip is a suite that stops catching things.

The throttle's own behaviour is not skipped, it is tested directly and
without waiting, by injecting a clock and a sleep function (see
tests/data_layer/test_alpha_vantage.py). What is disabled here is only
the wall-clock cost in every OTHER test that happens to route through a
provider call.
"""

from __future__ import annotations

import pytest

from equity_analyzer.data_layer import alpha_vantage


@pytest.fixture(autouse=True)
def _no_provider_throttle(monkeypatch):
    monkeypatch.setattr(alpha_vantage, "MIN_SECONDS_BETWEEN_REQUESTS", 0.0)
    alpha_vantage.reset_throttle()
    yield
    alpha_vantage.reset_throttle()
