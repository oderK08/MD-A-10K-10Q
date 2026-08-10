"""
One provider, two callers, one rate limit.

WHY THIS MODULE EXISTS, and it is worth being blunt about because the
first version shipped without it and the first real run died on it. Two
different modules talk to Alpha Vantage: `earnings_expectations` for the
consensus and `transcript_source` for the call. Neither knew about the
other, so a report fired both requests in the same millisecond, and the
free tier answered the second one with:

    "Please consider spreading out your free API requests more sparingly
     (1 request per second) ... raise the per-second burst limit"

The consensus had already arrived, so the failure looked like a missing
transcript rather than what it was. The rate limit belongs to the
PROVIDER, not to either caller, so the throttle has to live somewhere
both of them pass through. This is the same discipline `EdgarClient`
already applies to SEC: throttle on the way out, never rely on a caller
to remember.

THE GAP IS DELIBERATELY GENEROUS. The vendor's message names one request
per second, but its free tier has also been observed enforcing a
per-minute ceiling, and the message does not distinguish them. A report
makes two Alpha Vantage requests in the ordinary case, so waiting is
worth a few seconds of wall clock and nothing else; guessing the tightest
legal gap would save nothing and risks the exact failure this exists to
prevent.

A REFUSAL IS NOT ALWAYS TERMINAL, which is the other half of the same
story. Alpha Vantage answers a burst-limit hit and an exhausted daily
budget with the same HTTP 200 and overlapping prose, so they cannot be
told apart by reading the message. They demand opposite reactions: the
first clears in seconds, the second lasts until tomorrow. `retry_after`
below encodes the only honest response, which is to wait once and ask
again: a burst limit then succeeds, a spent budget fails identically and
the caller falls back.
"""

from __future__ import annotations

import time
from typing import Optional

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

# Minimum wall clock between any two requests to this provider. See the
# module docstring for why this is not one second.
MIN_SECONDS_BETWEEN_REQUESTS = 15.0

# How long to wait before asking again after a refusal, when the caller
# chooses to retry. Longer than the gap above: a refusal means we have
# already been told we are going too fast.
RETRY_AFTER_REFUSAL_SECONDS = 20.0

# Keys under which the vendor returns a refusal alongside HTTP 200: an
# exhausted quota, a burst limit, a premium-only endpoint, an
# unrecognised symbol. None of these is signalled by a status code, so
# `response.ok` is true for every one of them.
SOFT_ERROR_KEYS = ("Information", "Note", "Error Message")

_last_request_at: Optional[float] = None


def soft_error(payload) -> Optional[str]:
    """
    The vendor's refusal message, or None when the payload is real data.

    Checked BEFORE looking for any expected field, because otherwise a
    quota problem surfaces as "no usable 'transcript' field" and points
    the reader at field names when the real problem is the rate limit.
    That exact confusion cost a debugging round on the first live run.
    """
    if not isinstance(payload, dict):
        return None
    for key in SOFT_ERROR_KEYS:
        message = payload.get(key)
        if message:
            return f"{key} : {str(message)[:300]}"
    return None


def throttle(sleep=time.sleep, now=time.monotonic) -> float:
    """
    Blocks until `MIN_SECONDS_BETWEEN_REQUESTS` have passed since the
    last call, then records this one. Returns how long it waited, so a
    caller can say so rather than appearing to hang.

    `sleep` and `now` are injectable purely so the tests can verify the
    spacing without actually spending the time.
    """
    global _last_request_at

    waited = 0.0
    if _last_request_at is not None:
        elapsed = now() - _last_request_at
        remaining = MIN_SECONDS_BETWEEN_REQUESTS - elapsed
        if remaining > 0:
            sleep(remaining)
            waited = remaining
    _last_request_at = now()
    return waited


def reset_throttle() -> None:
    """Forgets the last request. For tests, and for a fresh process."""
    global _last_request_at
    _last_request_at = None


__all__ = [
    "ALPHA_VANTAGE_URL",
    "MIN_SECONDS_BETWEEN_REQUESTS",
    "RETRY_AFTER_REFUSAL_SECONDS",
    "SOFT_ERROR_KEYS",
    "reset_throttle",
    "soft_error",
    "throttle",
]
