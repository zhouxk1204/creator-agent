"""Parse a pasted Xiaohongshu target (URL / share text / bare note id) into a note id.

Accepts anything the clipboard is likely to hold after "分享 → 复制链接" in the
Xiaohongshu app:

- canonical note URLs: ``https://www.xiaohongshu.com/explore/<note_id>``
- discovery item URLs: ``https://www.xiaohongshu.com/discovery/item/<note_id>``
- short links: ``http://xhslink.com/xxxx`` (resolved via HTTP redirect)
- full share text: ``... 看看【xxx】 😆 http://xhslink.com/xxxx 复制本条信息...``
- bare 24-char hex note ids

Unlike Douyin, a note URL carries an ``xsec_token`` query parameter that the
note page needs in order to render for a logged-out visitor. The token is minted
per share, so it can never be invented here: when the input has no token (a bare
id) the canonical URL is still built, but the page may fall back to the login /
"note not found" state. Always prefer pasting the full share link.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from creator_agent.collector.douyin.meta import USER_AGENT
from creator_agent.collector.xhs.meta import XHS_REFERER

logger = logging.getLogger(__name__)

_SHORT_LINK_HOSTS = ("xhslink.com",)
_URL_RE = re.compile(r"https?://[^\s\"'，。、]+")
# Note ids are 24 lowercase hex chars (a Mongo ObjectId).
_NOTE_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
# /explore/<id>, /discovery/item/<id> and the "open in app" share pages.
_PATH_ID_RE = re.compile(r"/(?:explore|discovery/item|item|note)/([0-9a-fA-F]{24})")
# Only these survive normalization: everything else in a share link is
# app-side tracking (apptime, track_code, wechatWid, ...) that the web page
# ignores - and a stale ``xsec_source`` is fine, but dropping it entirely is not
# (the note page 404s without it when one was present).
_KEEP_QUERY = ("xsec_token", "xsec_source", "type")
XHS_HOSTS = ("xiaohongshu.com", "xhslink.com")


class XhsUrlError(ValueError):
    """Raised when no Xiaohongshu note id can be extracted from the input."""


def is_xhs_url(raw: str) -> bool:
    """True if ``raw`` looks like a Xiaohongshu link (used to pick a collector)."""
    if not raw:
        return False
    m = _URL_RE.search(raw)
    candidate = m.group(0) if m else raw.strip()
    host = urlparse(candidate if "://" in candidate else f"https://{candidate}").netloc.lower()
    # Match the domain itself or a subdomain of it - never a host that merely
    # ends with the string (``notxiaohongshu.com``).
    return any(host == h or host.endswith(f".{h}") for h in XHS_HOSTS)


def parse_note_target(raw: str, timeout_sec: int = 15) -> tuple[str, str]:
    """Return ``(note_id, page_url)`` for any pasted Xiaohongshu target.

    ``timeout_sec`` bounds the short-link redirect resolution.
    """
    raw = (raw or "").strip()
    if not raw:
        raise XhsUrlError("empty input - paste a Xiaohongshu URL, share text, or note id")

    if _NOTE_ID_RE.match(raw):
        return raw.lower(), _canonical_url(raw.lower())

    # Share text wraps the link in prose; pull the first URL out of it.
    m = _URL_RE.search(raw)
    candidate = m.group(0).rstrip("/") if m else raw

    if any(host in candidate for host in _SHORT_LINK_HOSTS):
        candidate = _resolve_short_link(candidate, timeout_sec)

    match = _PATH_ID_RE.search(candidate)
    if not match:
        raise XhsUrlError(
            f"链接里没有小红书笔记 id: {raw!r}\n"
            "  只支持笔记链接（/explore/<id> 或分享短链 xhslink.com/...），主页链接不支持。"
        )

    note_id = match.group(1).lower()
    return note_id, _canonical_url(note_id, source_url=candidate)


def _canonical_url(note_id: str, source_url: str = "") -> str:
    """``/explore/<id>`` plus any token params worth carrying over.

    Query parts are copied verbatim (still percent-encoded) rather than parsed
    and re-encoded: the server validates ``xsec_token`` as an opaque string, so
    a decode/encode round trip is a needless way to break it.
    """
    url = f"https://www.xiaohongshu.com/explore/{note_id}"
    if not source_url:
        return url
    kept = [part for part in urlparse(source_url).query.split("&") if part and part.split("=", 1)[0] in _KEEP_QUERY]
    return f"{url}?{'&'.join(kept)}" if kept else url


def _resolve_short_link(url: str, timeout_sec: int) -> str:
    """Follow an ``xhslink.com`` short link to its landing note URL.

    Xiaohongshu answers with a 302 to ``www.xiaohongshu.com/explore/<id>?xsec_token=...``.
    We read the redirect target rather than the final response, because the
    landing page itself is a JS shell with no note id in the body if the token
    has already been consumed.
    """
    headers = {"User-Agent": USER_AGENT, "Referer": XHS_REFERER}
    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=False, headers=headers) as client:
            resp = client.get(url)
    except Exception as e:
        raise XhsUrlError(f"failed to resolve short link {url}: {e}") from e

    location = resp.headers.get("location") or ""
    if _PATH_ID_RE.search(location):
        return location
    if resp.is_redirect and location:
        # One more hop (some share ids bounce through a /share/ page).
        try:
            with httpx.Client(timeout=timeout_sec, follow_redirects=True, headers=headers) as client:
                return str(client.get(location).url)
        except Exception as e:
            raise XhsUrlError(f"failed to follow {location}: {e}") from e

    logger.warning("Short link %s returned %s without a note id", url, resp.status_code)
    raise XhsUrlError(
        f"短链 {url} 没有跳转到笔记页（HTTP {resp.status_code}）。"
        "链接可能已过期，请在 App 里重新「分享 → 复制链接」后再试。"
    )
