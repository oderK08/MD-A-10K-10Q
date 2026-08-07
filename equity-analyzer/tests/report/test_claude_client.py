"""
The transport. Every failure has to arrive as an exception carrying a
reason, because the caller's only alternative is to print something in
the space page 1 reserves for a real reading.
"""

from __future__ import annotations

import pytest

from equity_analyzer.report import claude_client
from equity_analyzer.report.claude_client import ClaudeError, accepts_temperature, call_claude


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _post_returning(response, captured=None):
    def _post(*args, **kwargs):
        if captured is not None:
            captured.append(kwargs.get("json", {}))
        return response
    return _post


def test_returns_the_models_text(monkeypatch):
    monkeypatch.setattr(
        claude_client.requests, "post",
        _post_returning(_Response(payload={"content": [{"text": "  une lecture  "}]})),
    )
    assert call_claude("p", api_key="k", system_prompt="s") == "une lecture"


def test_the_system_prompt_and_the_model_are_sent_as_given(monkeypatch):
    sent = []
    monkeypatch.setattr(
        claude_client.requests, "post",
        _post_returning(_Response(payload={"content": [{"text": "ok"}]}), sent),
    )
    call_claude("mon prompt", api_key="k", system_prompt="mon system", model="claude-opus-5")

    assert sent[0]["system"] == "mon system"
    assert sent[0]["model"] == "claude-opus-5"
    assert sent[0]["messages"][0]["content"] == "mon prompt"


def test_temperature_is_omitted_for_the_claude_5_family(monkeypatch):
    """
    Those models reject the parameter outright with an HTTP 400, so
    sending it unconditionally makes them unusable. Found the worst
    possible way: after the transcript was already fetched and the
    quota already spent.
    """
    sent = []
    monkeypatch.setattr(
        claude_client.requests, "post",
        _post_returning(_Response(payload={"content": [{"text": "ok"}]}), sent),
    )
    call_claude("p", api_key="k", system_prompt="s", model="claude-sonnet-5")
    assert "temperature" not in sent[0]


def test_temperature_is_still_sent_where_it_is_supported(monkeypatch):
    sent = []
    monkeypatch.setattr(
        claude_client.requests, "post",
        _post_returning(_Response(payload={"content": [{"text": "ok"}]}), sent),
    )
    call_claude("p", api_key="k", system_prompt="s", model="claude-haiku-4-5-20251001")
    assert sent[0]["temperature"] == 0


def test_the_model_family_check_is_a_pattern_not_a_list_of_known_ids():
    """
    Model ids change with every release. An allow-list would be correct
    the day it was written and wrong the next time a model ships.
    """
    assert accepts_temperature("claude-haiku-4-5-20251001") is True
    assert accepts_temperature("claude-opus-5") is False
    assert accepts_temperature("claude-sonnet-5-some-future-suffix") is False


def test_no_api_key_fails_before_the_request(monkeypatch):
    def _explode(*args, **kwargs):
        raise AssertionError("no request should be made without a key")

    monkeypatch.setattr(claude_client.requests, "post", _explode)
    with pytest.raises(ClaudeError):
        call_claude("p", api_key="", system_prompt="s")


def test_an_http_error_carries_the_status_and_the_body(monkeypatch):
    monkeypatch.setattr(
        claude_client.requests, "post",
        _post_returning(_Response(status_code=401, text="invalid x-api-key")),
    )
    with pytest.raises(ClaudeError) as exc:
        call_claude("p", api_key="k", system_prompt="s")
    assert "401" in str(exc.value)
    assert "invalid x-api-key" in str(exc.value)


def test_an_unexpected_response_shape_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(
        claude_client.requests, "post",
        _post_returning(_Response(payload={"unexpected": True})),
    )
    with pytest.raises(ClaudeError):
        call_claude("p", api_key="k", system_prompt="s")


def test_an_empty_answer_is_an_error(monkeypatch):
    """
    An empty string would flow downstream and render as a blank page 1,
    which reads as a company with nothing to say.
    """
    monkeypatch.setattr(
        claude_client.requests, "post",
        _post_returning(_Response(payload={"content": [{"text": "   "}]})),
    )
    with pytest.raises(ClaudeError):
        call_claude("p", api_key="k", system_prompt="s")


def test_a_network_failure_is_an_error_with_its_cause(monkeypatch):
    def _raise(*args, **kwargs):
        raise claude_client.requests.RequestException("connection reset")

    monkeypatch.setattr(claude_client.requests, "post", _raise)
    with pytest.raises(ClaudeError) as exc:
        call_claude("p", api_key="k", system_prompt="s")
    assert "connection reset" in str(exc.value)
