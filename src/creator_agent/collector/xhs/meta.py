"""Resolve Xiaohongshu note metadata + video URL from a note page in one navigation.

Xiaohongshu's note page is server-side rendered: the whole note ships in the
document as ``<script>window.__INITIAL_STATE__ = {...}</script>``, including
``note.noteDetailMap`` with title, author, stats, tags, cover, duration and the
direct CDN video URL. One navigation + one blob parse yields everything.

Two findings from live probing (2026-09) drove the design:

- **Do not read the state via ``page.evaluate``.** Under XHS's IP risk control
  (error 300012) the SPA's JavaScript never finishes booting, so
  ``window.__INITIAL_STATE__`` stays undefined on the live page while the same
  JSON sits complete in the raw HTML. Parse the *document body* captured from
  the network layer instead - which also makes the whole thing faster, since
  there is no 20s wait for JS.
- **Plain httpx cannot fetch the note page.** Even with the profile's cookies
  the server 302s to /login (fingerprint-based), so Playwright is required for
  the document. The *CDN download* however works fine from the same IP via
  httpx once the signed URL is known.

The blob is a JavaScript object literal, not JSON (it contains bare
``undefined``); it is extracted by brace-matching and the ``undefined`` tokens
are mapped to ``null`` before ``json.loads``. An intercepted
``sns-video-*.xhscdn.com`` mp4 response is kept as a fallback for the video URL,
because ``masterUrl`` is occasionally absent (older notes) while the player
still streams fine.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from playwright.sync_api import Page

if TYPE_CHECKING:
    from creator_agent.browser.manager import BrowserManager

__all__ = [
    "XHS_REFERER",
    "XhsNoteMeta",
    "diagnose_empty_state",
    "fetch_note_meta",
    "navigate_and_capture",
    "note_from_html",
    "note_to_meta",
    "pick_note",
    "resolve_video_url",
    "state_blob_from_html",
]

logger = logging.getLogger(__name__)

XHS_REFERER = "https://www.xiaohongshu.com/"
# The player streams the note's video from sns-video-{bd,hw,qc,v3}.xhscdn.com as
# a progressive mp4. Other xhscdn hosts serve images (sns-webpic-*), avatars
# (sns-avatar-*) and JS (fe-static-*), so the host must be matched by name -
# matching on "xhscdn.com" alone would grab a cover still as the video.
VIDEO_CDN_HOST_RE = re.compile(r"^sns-video[a-z0-9-]*\.xhscdn\.com$", re.IGNORECASE)
# Streams are named by codec; h264 is the most broadly compatible and is always
# present when the note has a video.
_CODEC_PREFERENCE = ("h264", "h265", "av1")
# Interaction counters arrive as ints, strings, or Chinese-suffixed strings
# ("1.2万"). Same for the duration on some note revisions.
_SUFFIXES = {"万": 10_000, "w": 10_000, "W": 10_000, "k": 1_000, "K": 1_000}
_STATE_MARKER = "window.__INITIAL_STATE__"
# XHS's risk-control endpoint the SPA navigates to when it decides the session
# is suspicious. Aborting the redirect keeps the (already complete) note page.
_RISK_URL_FRAGMENT = "website-login"


@dataclass
class XhsNoteMeta:
    """Metadata resolved from a Xiaohongshu note page (one browser navigation)."""

    cdn_url: str | None = None
    note_id: str = ""
    note_type: str = ""  # "video" | "normal"
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    likes: int = 0
    comments: int = 0
    shares: int = 0
    favorites: int = 0
    views: int | None = None
    duration_sec: int | None = None
    published_at: datetime | None = None
    cover_url: str | None = None
    author_uid: str = ""
    author_nickname: str = ""
    author_avatar: str | None = None


def fetch_note_meta(
    page_url: str,
    browser: BrowserManager,
    timeout_sec: int,
    note_id: str | None = None,
) -> XhsNoteMeta:
    """Navigate a note page once; resolve the CDN video URL and rich metadata.

    ``note_id`` (when the caller already parsed it out of the link) makes the
    extraction pick *that* note out of ``noteDetailMap`` instead of whatever
    entry the profile happened to visit first.

    Returns a :class:`XhsNoteMeta` with ``cdn_url`` unset when the note could not
    be read (expired ``xsec_token``, hard risk-control block, deleted note) -
    callers are expected to treat that as a hard failure with the message from
    :func:`diagnose_empty_state`.
    """
    note_state, cdn_url = navigate_and_capture(page_url, browser, timeout_sec, note_id=note_id)
    meta = note_to_meta(note_state, cdn_url)
    logger.info(
        "Fetched XHS note %s: type=%s cdn=%s likes=%s tags=%d desc_len=%d",
        meta.note_id or "?",
        meta.note_type or "?",
        bool(meta.cdn_url),
        meta.likes,
        len(meta.tags),
        len(meta.description),
    )
    return meta


def navigate_and_capture(
    page_url: str, browser: BrowserManager, timeout_sec: int, note_id: str | None = None
) -> tuple[dict | None, str | None]:
    """Navigate the note page; return ``(note_dict, intercepted_cdn_url)``.

    ``note_dict`` is the raw note object parsed out of the ``/explore``
    *document body* (None if no document carried it). The video URL intercept
    reads response metadata only - the video arrives as byte-range fragments,
    so the body is never touched.
    """
    media_url: str | None = None
    doc_bodies: list[str] = []

    def on_response(response) -> None:
        nonlocal media_url
        try:
            url = response.url
            ctype = response.headers.get("content-type", "")
            if media_url is None and VIDEO_CDN_HOST_RE.match(urlparse(url).netloc):
                if "video/mp4" in ctype or ".mp4" in urlparse(url).path:
                    media_url = url
                return
            if "/explore/" in url and "text/html" in ctype:
                body = response.body().decode("utf-8", "replace")
                if _STATE_MARKER in body:
                    doc_bodies.append(body)
        except Exception:
            # A response may arrive as the page tears down, or the body may
            # still be streaming; never let a handler error abort navigation.
            pass

    def on_route(route, request) -> None:
        try:
            if _RISK_URL_FRAGMENT in request.url:
                logger.warning("XHS risk-control redirect blocked: %s", request.url[:120])
                route.abort()
            else:
                route.continue_()
        except Exception:
            # If routing fails, prefer not to kill the request.
            try:
                route.continue_()
            except Exception:
                pass

    page: Page = browser.new_page()
    page.route("**/*", on_route)
    page.on("response", on_response)
    try:
        page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
        # The document usually arrives with the navigation; the poll covers a
        # document that streams in late or a first body that lacked the blob.
        note: dict | None = None
        deadline = time.monotonic() + min(timeout_sec, 20)
        while note is None and time.monotonic() < deadline:
            for body in doc_bodies:
                note = note_from_html(body, note_id)
                if note is not None:
                    break
            if note is None:
                page.wait_for_timeout(500)
        # The media stream lags the document; give the intercept a short grace
        # window (the blob's masterUrl is the primary source, this is a fallback).
        if media_url is None and note is not None:
            page.wait_for_timeout(3000)
    finally:
        page.close()

    return note, media_url


def note_from_html(html: str, note_id: str | None = None) -> dict | None:
    """The note object parsed out of a raw ``/explore`` document (or None)."""
    blob = state_blob_from_html(html)
    if blob is None:
        return None
    # The blob is a JS object literal; the only non-JSON token XHS emits is
    # ``undefined`` (e.g. ``redId:undefined``), which maps cleanly to null.
    # ``/`` escapes and ``\/`` are valid JSON and handled by json.loads.
    try:
        state = json.loads(re.sub(r"\bundefined\b", "null", blob))
    except json.JSONDecodeError as e:
        logger.warning("Could not parse __INITIAL_STATE__ blob: %s", e)
        return None
    return pick_note(state, note_id)


def state_blob_from_html(html: str) -> str | None:
    """Extract the raw object literal assigned to ``window.__INITIAL_STATE__``.

    Brace-matched rather than regexed so a ``}`` inside a string value cannot
    cut the blob short. Returns None when the marker or its object is absent.
    """
    marker = html.find(_STATE_MARKER)
    if marker < 0:
        return None
    start = html.find("{", marker)
    if start < 0:
        return None

    depth = 0
    quote = ""
    escaped = False
    for i in range(start, len(html)):
        ch = html[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in ('"', "'"):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    return None


def pick_note(state: dict | None, note_id: str | None = None) -> dict | None:
    """The note object from a parsed ``__INITIAL_STATE__`` (or None).

    ``noteDetailMap`` accumulates every note the browser profile has opened, so
    when ``note_id`` is known it is used as the key directly. Without it the map
    is walked in insertion order and the first *usable* note wins.
    """
    if not isinstance(state, dict):
        return None
    note_root = state.get("note")
    detail_map = (note_root.get("noteDetailMap") or {}) if isinstance(note_root, dict) else {}
    if not isinstance(detail_map, dict):
        return None
    if note_id and isinstance(detail_map.get(note_id), dict):
        note = detail_map[note_id].get("note")
        if _is_usable_note(note):
            return note
    for entry in detail_map.values():
        note = (entry or {}).get("note") if isinstance(entry, dict) else None
        if _is_usable_note(note):
            return note
    return None


def _is_usable_note(note) -> bool:
    return isinstance(note, dict) and bool(note.get("noteId") or note.get("video"))


def note_to_meta(note: dict | None, cdn_url: str | None = None) -> XhsNoteMeta:
    """Map a raw note object (plus any intercepted URL) onto :class:`XhsNoteMeta`."""
    note = note or {}
    user = _as_dict(note.get("user"))
    interact = _as_dict(note.get("interactInfo"))
    video = _as_dict(note.get("video"))
    image_list = _as_list(note.get("imageList"))

    title = str(note.get("title") or "").strip()
    # In-text hashtags are marked "#健康科普[话题]#"; the marker is noise in any
    # downstream text use (transcript pairing, reports), so strip it.
    desc = str(note.get("desc") or "").replace("[话题]", "").strip()
    # Image/text notes have no field split: title is empty and desc holds
    # everything. Fall back to desc so the video always has a name to show and
    # to use as the download filename (Douyin does the same with ``desc``).
    if not title:
        title = desc

    return XhsNoteMeta(
        cdn_url=resolve_video_url(note) or cdn_url,
        note_id=note.get("noteId") or "",
        note_type=note.get("type") or "",
        title=title,
        description=desc,
        tags=[t["name"] for t in _as_list(note.get("tagList")) if isinstance(t, dict) and t.get("name")],
        likes=_to_int(interact.get("likedCount")),
        comments=_to_int(interact.get("commentCount")),
        shares=_to_int(interact.get("shareCount")),
        favorites=_to_int(interact.get("collectedCount")),
        views=None,  # Xiaohongshu exposes no public play count.
        duration_sec=_duration_sec(video),
        published_at=_published_at(note),
        cover_url=_cover_url(image_list, video),
        author_uid=user.get("userId") or "",
        author_nickname=str(user.get("nickname") or "").strip(),
        author_avatar=user.get("avatar") or None,
    )


def resolve_video_url(note: dict) -> str | None:
    """The direct CDN mp4 URL for a note, or None if there is no video.

    Preference order: the best h264 ``masterUrl`` (widest device support), then
    h265/av1, then the origin key reconstructed against the CDN host - which is
    what ``masterUrl`` encodes anyway and works when a note carries no
    ``stream`` block.
    """
    video = _as_dict(note.get("video"))
    media = _as_dict(video.get("media"))
    stream = _as_dict(media.get("stream"))
    for codec in _CODEC_PREFERENCE:
        entries = [_as_dict(e) for e in _as_list(stream.get(codec))]
        entries = [e for e in entries if e.get("masterUrl") or _as_list(e.get("backupUrls"))]
        if entries:
            # The same codec ships in several quality tiers (streamType 259/261
            # ...); size tracks bitrate x duration, so the biggest entry is the
            # best copy for downstream analysis.
            best = max(entries, key=lambda e: _to_int(e.get("size")))
            url = best.get("masterUrl") or _as_list(best.get("backupUrls"))[0]
            return _to_https(str(url))
    origin_key = _as_dict(video.get("consumer")).get("originVideoKey")
    if origin_key:
        return f"https://sns-video-bd.xhscdn.com/{origin_key}"
    return None


def diagnose_empty_state(page_url: str) -> str:
    """A user-facing hint for the "no note found" case."""
    return (
        f"未能从小红书笔记页解析出内容: {page_url}\n"
        "  常见原因: 1) 链接里的 xsec_token 已过期（App 里重新分享复制）; "
        "2) 笔记被删除或设为私密; 3) 当前 IP 触发了小红书风控（300012），"
        "页面只返回「安全限制」。\n"
        "  风控通常在几十分钟到几小时后自行解除，也可切换网络后重试；"
        "反复触发时先跑 uv run creator-agent auth-login --platform xiaohongshu 登录。"
    )


# -- internals --------------------------------------------------------------


def _as_dict(value) -> dict:
    """The page-owned JSON is untrusted: a field typed as an object can be
    null, a list or a string depending on the note revision."""
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _to_https(url: str) -> str:
    """Xiaohongshu hands out ``http://`` CDN URLs; https is safer to fetch."""
    return "https://" + url[len("http://") :] if url.startswith("http://") else url


def _to_int(value) -> int:
    """Parse a counter that may be ``int``, ``"1234"`` or ``"1.2万"``."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    suffix = text[-1]
    if suffix in _SUFFIXES:
        try:
            return int(float(text[:-1]) * _SUFFIXES[suffix])
        except ValueError:
            return 0
    digits = re.match(r"\d+", text)
    return int(digits.group(0)) if digits else 0


def _duration_sec(video: dict) -> int | None:
    """Note duration in seconds.

    ``capa.duration`` is seconds; the per-stream ``duration`` is milliseconds -
    told apart by magnitude, since a 1000s+ (16 min) note is rare but a 1000ms
    one is not.
    """
    capa = _to_int(_as_dict(video.get("capa")).get("duration"))
    if capa:
        return capa
    media = _as_dict(video.get("media"))
    stream = _as_dict(media.get("stream"))
    for codec in _CODEC_PREFERENCE:
        for entry in _as_list(stream.get(codec)):
            raw = _to_int(_as_dict(entry).get("duration"))
            if raw:
                return raw // 1000 if raw >= 1000 else raw
    return None


def _published_at(note: dict) -> datetime | None:
    """``note.time`` is epoch milliseconds."""
    raw = note.get("time") or note.get("lastUpdateTime")
    if not raw:
        return None
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return None
    if ts > 10_000_000_000:  # milliseconds
        ts //= 1000
    return datetime.fromtimestamp(ts, tz=UTC)


def _cover_url(image_list: list, video: dict) -> str | None:
    """The note poster.

    Current note revisions put the URL on ``infoList`` (one entry per image
    scene: WB_PRV preview, WB_DFT default); older ones use the flat urlDefault /
    urlPre keys. Flat keys win when both exist.
    """
    for image in _as_list(image_list):
        image = _as_dict(image)
        for key in ("urlDefault", "urlPre", "url"):
            if image.get(key):
                return _to_https(str(image[key]))
        infos = [_as_dict(i) for i in _as_list(image.get("infoList"))]
        for scene in ("WB_PRV", "WB_DFT"):
            for info in infos:
                if info.get("imageScene") == scene and info.get("url"):
                    return _to_https(str(info["url"]))
        for info in infos:
            if info.get("url"):
                return _to_https(str(info["url"]))
    image = _as_dict(video.get("image"))
    for key in ("firstFrame", "thumbnail"):
        url = image.get(key)
        if isinstance(url, str) and url.startswith("http"):
            return _to_https(url)
    return None
