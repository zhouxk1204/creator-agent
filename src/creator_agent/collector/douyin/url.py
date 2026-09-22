"""Parse a pasted Douyin target (URL / share text / bare id) into a video id.

Accepts anything the clipboard is likely to hold after "分享 → 复制链接" in
the Douyin app:

- canonical page URLs: ``https://www.douyin.com/video/<id>``
- modal URLs carrying ``modal_id=<id>``
- short links: ``https://v.douyin.com/xxxx/`` (resolved via HTTP redirect)
- full share text: ``7.94 复制打开抖音，看看【...】 https://v.douyin.com/xxxx/ ...``
- bare numeric ids

The single entry point :func:`parse_video_target` returns ``(video_id,
page_url)`` where ``page_url`` is the canonical ``/video/<id>`` URL used for
browser navigation.
"""

from __future__ import annotations

import logging
import re

import httpx

from creator_agent.collector.douyin.meta import USER_AGENT

logger = logging.getLogger(__name__)

_SHORT_LINK_HOSTS = ("v.douyin.com", "www.iesdouyin.com")
_URL_RE = re.compile(r"https?://[^\s\"'，。]+")


class DouyinUrlError(ValueError):
    """Raised when no Douyin video id can be extracted from the input."""


def parse_video_target(raw: str, timeout_sec: int = 15) -> tuple[str, str]:
    """Return ``(video_id, canonical_page_url)`` for any pasted Douyin target.

    ``timeout_sec`` bounds the short-link redirect resolution.
    """
    raw = (raw or "").strip()
    if not raw:
        raise DouyinUrlError("empty input - paste a Douyin URL, share text, or video id")

    if raw.isdigit():
        return raw, f"https://www.douyin.com/video/{raw}"

    # Share text wraps the link in prose; pull the first URL out of it.
    m = _URL_RE.search(raw)
    candidate = m.group(0).rstrip("/") if m else raw

    if any(host in candidate for host in _SHORT_LINK_HOSTS):
        candidate = _resolve_short_link(candidate, timeout_sec)

    match = (
        re.search(r"/video/(\d+)", candidate)
        or re.search(r"modal_id=(\d+)", candidate)
        or re.search(r"(\d{10,})", candidate)
    )
    if not match:
        raise DouyinUrlError(f"could not find a Douyin video id in: {raw!r}")
    video_id = match.group(1)
    return video_id, f"https://www.douyin.com/video/{video_id}"


def _resolve_short_link(url: str, timeout_sec: int) -> str:
    """Follow a ``v.douyin.com`` short link to its landing URL.

    Normally a 302 chain ending at ``www.douyin.com/video/<id>`` (or a modal
    URL with ``modal_id``). Some links land on an interstitial page instead, in
    which case we grep the HTML for an aweme id.
    """
    headers = {"User-Agent": USER_AGENT, "Referer": "https://www.douyin.com/"}
    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
    except Exception as e:
        raise DouyinUrlError(f"failed to resolve short link {url}: {e}") from e

    final_url = str(resp.url)
    if re.search(r"/video/(\d+)|modal_id=(\d+)", final_url):
        return final_url

    # Interstitial page: look for an aweme id in the body.
    body = resp.text or ""
    m = re.search(r'"aweme_id"\s*:\s*"(\d+)"', body) or re.search(r"/video/(\d+)", body)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    logger.warning("Short link %s resolved to %s but no video id found", url, final_url)
    return final_url
