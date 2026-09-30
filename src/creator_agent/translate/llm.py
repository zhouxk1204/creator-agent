"""Shared OpenAI-compatible chat client for the translate tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from creator_agent.config import TranslateSettings


def chat(settings: TranslateSettings, system: str, user: str, temperature: float = 0.2) -> str:
    """One chat-completions call against the configured local server
    (Ollama / llama.cpp / vLLM / LM Studio). Returns the message content."""
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
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]
