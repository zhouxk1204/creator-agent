"""Resolve Douyin video metadata + CDN URL from a video page in one navigation.

Shared by :class:`~creator_agent.downloader.downloader.Downloader` and
:class:`~creator_agent.collector.douyin.collector.DouyinCollector` so the
hard-won capture logic (intercept the ``video/mp4`` CDN response + the
``aweme/detail`` XHR) lives in exactly one place.

The creator-homepage grid card carries no publish time, so the only way to get
``published_at`` (needed for the collect time-window filter) is to navigate the
video page and read the aweme-detail JSON - which also yields the CDN URL, stats,
tags, duration and cover for free.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from playwright.sync_api import Page

if TYPE_CHECKING:
    from creator_agent.browser.manager import BrowserManager

logger = logging.getLogger(__name__)

# Douyin's web player fetches each video as a direct video/mp4 from a CDN host
# like ``v26-web.douyinvod.com`` (NOT m3u8/ts). The URL carries an expiring
# signature, so it must be captured and downloaded in the same sync run.
VIDEO_CDN_HOSTS = ("douyinvod.com",)
# Effect/overlay mp4s are served alongside the main video and must be ignored,
# otherwise the intercept grabs an overlay instead of the real video.
EFFECT_CDN_HOSTS = ("byteeffecttos.com", "effectcdn")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DOUYIN_REFERER = "https://www.douyin.com/"
# XHR endpoint Douyin's video page calls for the full aweme detail (desc, stats,
# tags, duration, cover, create_time).
DETAIL_URL_FRAGMENT = "aweme/detail"


@dataclass
class VideoMeta:
    """Metadata resolved from a Douyin video page (one browser navigation)."""

    cdn_url: str | None = None
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


def is_direct_media_url(url: str) -> bool:
    return url.endswith(".mp4") or any(h in url for h in VIDEO_CDN_HOSTS)


def fetch_video_meta(page_url: str, browser: BrowserManager, timeout_sec: int) -> VideoMeta:
    """Navigate a Douyin video page once and resolve both the direct CDN URL
    and the video metadata (title/description/tags/stats/duration/cover).

    The aweme detail is read from the ``/aweme/v1/web/aweme/detail/`` XHR the
    page fires. If the detail response is not captured, the returned
    :class:`VideoMeta` has empty metadata fields (and ``cdn_url`` may still be
    set, so download can proceed).
    """
    cdn_url, detail = navigate_and_capture(page_url, browser, timeout_sec)
    meta = aweme_to_meta(cdn_url, detail)
    logger.info(
        "Fetched meta for %s: cdn=%s, likes=%s, tags=%d, desc_len=%d",
        page_url,
        bool(cdn_url),
        meta.likes,
        len(meta.tags),
        len(meta.description),
    )
    return meta


def navigate_and_capture(page_url: str, browser: BrowserManager, timeout_sec: int) -> tuple[str | None, dict | None]:
    """Navigate the video page; capture the direct CDN mp4 URL and the aweme
    detail JSON in a single navigation.

    Only response metadata (url/headers) is read in the handler, except for
    the aweme detail response whose body is JSON (small, complete -- unlike
    the video stream which arrives as byte-range fragments).
    """
    cdn_url: str | None = None
    detail: dict | None = None

    def on_response(response) -> None:
        nonlocal cdn_url, detail
        try:
            url = response.url
            ctype = response.headers.get("content-type", "")
            if cdn_url is None and ("video/mp4" in ctype or url.endswith(".mp4")):
                if any(h in url for h in VIDEO_CDN_HOSTS) and not any(h in url for h in EFFECT_CDN_HOSTS):
                    cdn_url = url
            if detail is None and DETAIL_URL_FRAGMENT in url and "json" in ctype:
                body = response.body().decode("utf-8", "replace")
                data = json.loads(body)
                aw = data.get("aweme_detail") if isinstance(data, dict) else None
                if aw and "statistics" in aw:
                    detail = aw
        except Exception:
            # A response may arrive as the page tears down, or the body may
            # still be streaming; never let a handler error abort navigation.
            pass

    page: Page = browser.new_page()
    page.on("response", on_response)
    try:
        page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
        page.wait_for_timeout(5000)
        if cdn_url is None or detail is None:
            # The <video> element may be lazy; kick the player so it requests
            # the media stream and the detail XHR flushes.
            try:
                page.evaluate('document.querySelector("video")?.play()')
            except Exception:
                pass
            page.wait_for_timeout(5000)
    finally:
        page.close()

    return cdn_url, detail


def aweme_to_meta(cdn_url: str | None, aw: dict | None) -> VideoMeta:
    aw = aw or {}
    stats = aw.get("statistics") or {}
    tags = [t.get("hashtag_name") for t in (aw.get("text_extra") or []) if t.get("hashtag_name")]
    duration_ms = aw.get("duration")
    create_time = aw.get("create_time")
    video = aw.get("video") or {}
    cover_url_list = (video.get("cover") or {}).get("url_list") or []
    desc = aw.get("desc") or ""
    # Prefer the play_addr from the aweme detail as the download URL: it is a
    # muxed mp4 (audio + video). The intercepted network URL is unreliable --
    # Douyin's web player fetches separate DASH video/audio streams, so the
    # intercept grabs whichever media response arrives first (sometimes a
    # video-only stream, sometimes an audio fragment), yielding a silent file.
    # Decode any literal ampersand-escape survivors: Douyin JSON-escapes ``&``
    # (json.loads handles the standard form, but Douyin sometimes double-encodes).
    play_addr_list = (video.get("play_addr") or {}).get("url_list") or []
    resolved_url = play_addr_list[0].replace("\\u0026", "&") if play_addr_list else cdn_url
    return VideoMeta(
        cdn_url=resolved_url,
        title=desc,
        description=desc,
        tags=tags,
        likes=int(stats.get("digg_count") or 0),
        comments=int(stats.get("comment_count") or 0),
        shares=int(stats.get("share_count") or 0),
        favorites=int(stats.get("collect_count") or 0),
        views=int(stats["play_count"]) if stats.get("play_count") else None,
        duration_sec=duration_ms // 1000 if duration_ms else None,
        published_at=datetime.fromtimestamp(create_time, tz=UTC) if create_time else None,
        cover_url=cover_url_list[0] if cover_url_list else None,
    )
