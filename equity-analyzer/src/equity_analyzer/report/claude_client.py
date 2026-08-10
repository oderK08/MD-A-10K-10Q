"""
The Claude API call, and nothing else.

Split out from the module that used to own it so that the transport --
the HTTP shape, the error taxonomy, the model-compatibility quirk below
-- is not tied to one particular prompt. There is one thing in this
project that talks to the model; there should be one place that knows
how.

WHY EVERY FAILURE IS AN EXCEPTION AND NEVER A PLACEHOLDER STRING. A
report that degrades to "analysis unavailable" text, set in the same
type as a real reading, is how a reader ends up trusting an empty page.
The caller decides what to do with the absence; this module refuses to
invent one.
"""

from __future__ import annotations

import re
from typing import Optional

import requests

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

# Reading a full earnings call and weighing it against consensus is a
# judgement task, not a restatement task, so the default is a reasoning
# model rather than the cheapest one. Overridable per call and from the
# workflow's dropdown.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_TIMEOUT_SECONDS = 120.0


class ClaudeError(Exception):
    """Any reason the model could not be asked, or did not usefully answer."""


# The Claude 5 family rejects `temperature` outright (HTTP 400,
# "`temperature` is deprecated for this model"), so sending it
# unconditionally makes those models unusable. On older models it is
# worth keeping: temperature=0 minimises run-to-run drift on a task
# whose grounding is entirely in the prompt.
#
# Matched on the family-and-generation pattern rather than an allow-list
# of exact model ids: ids change with every release, and a 400 on an
# unknown-but-current model is the worst possible moment to find out --
# the transcript is already fetched and the quota already spent by then.
_NO_TEMPERATURE_RE = re.compile(r"claude-(?:opus|sonnet|haiku|fable)-[5-9]\b", re.IGNORECASE)


def accepts_temperature(model: str) -> bool:
    return not _NO_TEMPERATURE_RE.search(model or "")


def _block_types(payload) -> str:
    """The `type` of every content block, for an error message worth reading."""
    blocks = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return ""
    return ", ".join(str(b.get("type", "?")) for b in blocks if isinstance(b, dict))


def _text_of(payload) -> str:
    """
    Every text block in the answer, joined, in order.

    NOT `content[0]["text"]`, which is what this did until a real PLTR
    run died on `KeyError: 'text'` with the transcript already fetched
    and paid for. The Messages API returns a LIST of content blocks and
    only some of them are text; a block of another type sitting first
    is a perfectly valid response, and reaching blindly into position
    zero turns it into a crash at the most expensive possible moment.

    Reading every text block rather than the first one is also the right
    answer when there are several: taking one would silently drop the
    rest of the reading.
    """
    blocks = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return ""
    parts = [
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part).strip()


def call_claude(
    prompt: str,
    *,
    api_key: str,
    system_prompt: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2000,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """
    Sends one message and returns the model's text. Raises ClaudeError on
    anything else.
    """
    if not api_key:
        raise ClaudeError("ANTHROPIC_API_KEY absent")

    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                **({"temperature": 0} if accepts_temperature(model) else {}),
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise ClaudeError(f"erreur réseau vers l'API Claude : {exc}") from exc

    if response.status_code != 200:
        raise ClaudeError(
            f"l'API Claude a renvoyé HTTP {response.status_code} : {response.text[:300]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ClaudeError(f"l'API Claude a renvoyé du non-JSON : {exc}") from exc

    text = _text_of(payload)
    if not text:
        raise ClaudeError(
            "l'API Claude n'a renvoyé aucun bloc de texte "
            f"(blocs : {_block_types(payload) or 'aucun'}, "
            f"stop_reason : {payload.get('stop_reason') if isinstance(payload, dict) else 'n/a'})"
        )
    return text


__all__ = [
    "ANTHROPIC_API_URL",
    "ANTHROPIC_API_VERSION",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "ClaudeError",
    "accepts_temperature",
    "call_claude",
]
