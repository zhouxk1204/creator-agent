"""Shared OpenAI-compatible chat client for the translate tools."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from creator_agent.config import TranslateSettings

# llama.cpp returns 503 while the model is still loading (or when all slots are
# momentarily busy). Retry a few times so a just-started server doesn't fail the
# whole run on its first request.
_MAX_503_RETRIES = 5
_503_BACKOFF_SEC = 5


def chat(settings: TranslateSettings, system: str, user: str, temperature: float = 0.2) -> str:
    """One chat-completions call against the configured local server
    (Ollama / llama.cpp / vLLM / LM Studio). Returns the message content."""
    resp: httpx.Response | None = None
    for attempt in range(_MAX_503_RETRIES + 1):
        resp = httpx.post(
            f"{settings.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            json={
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=settings.timeout_sec,
        )
        if resp.status_code != 503 or attempt == _MAX_503_RETRIES:
            break
        time.sleep(_503_BACKOFF_SEC)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]
