"""Unit tests for the shared LLM chat client (retry + proxy bypass)."""

from __future__ import annotations

import httpx
import pytest

from creator_agent.config import TranslateSettings
from creator_agent.translate import llm


def _resp(status: int, content: str | None = None) -> httpx.Response:
    url = "http://127.0.0.1:8080/v1/chat/completions"
    if content is not None:
        return httpx.Response(
            status, json={"choices": [{"message": {"content": content}}]}, request=httpx.Request("POST", url)
        )
    return httpx.Response(status, request=httpx.Request("POST", url))


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)


def test_chat_retries_transient_5xx_then_succeeds(monkeypatch, no_sleep):
    seen: list[dict] = []

    def fake_post(url, **kwargs):
        seen.append(kwargs)
        return _resp(502) if len(seen) < 3 else _resp(200, "你好")

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    assert llm.chat(TranslateSettings(), "system", "user") == "你好"
    assert len(seen) == 3
    # Local server calls must never be routed through a system/Clash proxy:
    # a proxy answers 502 Bad Gateway and fakes batch failures.
    assert all(kw["trust_env"] is False for kw in seen)


def test_chat_does_not_retry_client_errors(monkeypatch, no_sleep):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _resp(400)

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        llm.chat(TranslateSettings(), "system", "user")
    assert len(calls) == 1


def test_chat_gives_up_after_max_retries(monkeypatch, no_sleep):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _resp(503)

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        llm.chat(TranslateSettings(), "system", "user")
    assert len(calls) == llm._MAX_RETRIES + 1
