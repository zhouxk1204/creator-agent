"""Shared OpenAI-compatible chat client for the translate tools."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from creator_agent.config import TranslateSettings

# llama.cpp returns 503 while the model is still loading (or when all slots are
# momentarily busy) and 502/500 when a slot's task fails; all are worth retrying
# so a single hiccup doesn't cost a batch. Retrying beats translator.py's
# split-the-batch fallback, which loses cross-cue context and halves quality.
_RETRY_STATUS = {500, 502, 503, 504}
_MAX_RETRIES = 5
_BACKOFF_SEC = 5


def chat(settings: TranslateSettings, system: str, user: str, temperature: float = 0.2) -> str:
    """One chat-completions call against the configured local server
    (Ollama / llama.cpp / vLLM / LM Studio). Returns the message content."""
    resp: httpx.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
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
            # Never let a system/Clash proxy (HTTP_PROXY / ALL_PROXY) intercept
            # calls to the local server: a proxy answers 502 Bad Gateway when it
            # can't reach the target, which surfaces as spurious batch failures.
            trust_env=False,
        )
        if resp.status_code not in _RETRY_STATUS or attempt == _MAX_RETRIES:
            break
        print(
            f"[llm] {resp.status_code} from {settings.base_url}, "
            f"retry {attempt + 1}/{_MAX_RETRIES} in {_BACKOFF_SEC}s ...",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(_BACKOFF_SEC)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]
