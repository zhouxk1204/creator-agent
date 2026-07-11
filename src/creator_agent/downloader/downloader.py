from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from playwright.sync_api import Page

from creator_agent.models.creator import Creator
from creator_agent.models.video import Video

if TYPE_CHECKING:
    from creator_agent.browser.manager import BrowserManager
    from creator_agent.storage.file_storage import FileStorage

logger = logging.getLogger(__name__)

_DOWNLOAD_RETRIES = 3
_DOWNLOAD_TIMEOUT_S = 120

# Douyin's web player fetches each video as a direct video/mp4 from a CDN host
# like ``v26-web.douyinvod.com`` (NOT m3u8/ts). The URL carries an expiring
# signature, so it must be captured and downloaded in the same sync run.
_VIDEO_CDN_HOSTS = ("douyinvod.com",)
# Effect/overlay mp4s are served alongside the main video and must be ignored,
# otherwise the intercept grabs an overlay instead of the real video.
_EFFECT_CDN_HOSTS = ("byteeffecttos.com", "effectcdn")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_DOUYIN_REFERER = "https://www.douyin.com/"
# XHR endpoint Douyin's video page calls for the full aweme detail (desc, stats,
# tags, duration, cover, create_time).
_DETAIL_URL_FRAGMENT = "aweme/detail"


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


class Downloader:
    def __init__(
        self,
        storage: FileStorage,
        browser: BrowserManager,
        timeout_sec: int = _DOWNLOAD_TIMEOUT_S,
        retries: int = _DOWNLOAD_RETRIES,
    ) -> None:
        self._storage = storage
        self._browser = browser
        self._timeout = timeout_sec
        self._retries = retries

    def download_video(self, creator: Creator, video: Video, direct_url: str | None = None) -> Path:
        if not video.video_url and not direct_url:
            raise ValueError(f"Video {video.id} has no video_url")

        if direct_url:
            url = direct_url
            logger.info("Downloading video (pre-resolved URL): %s", url)
        else:
            url = str(video.video_url)
            logger.info("Downloading video: %s", url)
            # ``video_url`` from the Douyin collector is the video *page* URL, not
            # a direct media URL. Resolve the real CDN URL via the browser first.
            if not _is_direct_media_url(url):
                logger.info("video_url is a page URL; resolving direct CDN URL via browser...")
                url = self._resolve_direct_media_url(url) or ""
                if not url:
                    raise RuntimeError(
                        f"Could not resolve a direct video URL for video {video.id} from {video.video_url}"
                    )
                logger.info("Resolved direct URL: %s", url)

        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                data = self._httpx_get(url, referer=_DOUYIN_REFERER)
                path = self._storage.save_video_file(creator.id, video.id, data)
                logger.info("Video saved (%d bytes): %s", len(data), path)
                return path
            except Exception as e:
                last_exc = e
                logger.warning("Download attempt %d/%d failed: %s", attempt + 1, self._retries, e)
                if attempt < self._retries - 1:
                    time.sleep(2**attempt)

        raise RuntimeError(f"Video download failed after {self._retries} attempts: {last_exc}")

    def download_cover(self, creator: Creator, video: Video) -> Path | None:
        if not video.cover_url:
            logger.info("No cover_url for video %s, skipping.", video.id)
            return None
        url = str(video.cover_url)
        logger.info("Downloading cover: %s", url)
        for attempt in range(self._retries):
            try:
                data = self._httpx_get(url, referer=_DOUYIN_REFERER)
                path = self._storage.save_cover(creator.id, video.id, data)
                logger.info("Cover saved: %s", path)
                return path
            except Exception as e:
                logger.warning("Cover attempt %d/%d failed: %s", attempt + 1, self._retries, e)
                if attempt < self._retries - 1:
                    time.sleep(2**attempt)
        logger.error("Cover download failed after %d retries: %s", self._retries, url)
        return None

    def save_metadata(self, creator: Creator, video: Video) -> Path:
        path = self._storage.save_metadata(creator.id, video)
        logger.info("Metadata saved: %s", path)
        return path

    def fetch_video_meta(self, page_url: str) -> VideoMeta:
        """Navigate a Douyin video page once and resolve both the direct CDN URL
        and the video metadata (title/description/tags/stats/duration/cover).

        The aweme detail is read from the ``/aweme/v1/web/aweme/detail/`` XHR the
        page fires. If the detail response is not captured, the returned
        :class:`VideoMeta` has empty metadata fields (and ``cdn_url`` may still be
        set, so download can proceed).
        """
        cdn_url, detail = self._navigate_and_capture(page_url)
        meta = _aweme_to_meta(cdn_url, detail)
        logger.info(
            "Fetched meta for %s: cdn=%s, likes=%s, tags=%d, desc_len=%d",
            page_url,
            bool(cdn_url),
            meta.likes,
            len(meta.tags),
            len(meta.description),
        )
        return meta

    # -- internals ----------------------------------------------------------

    def _httpx_get(self, url: str, referer: str) -> bytes:
        cookies = self._browser.get_cookies("www.douyin.com")
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        headers = {"Referer": referer, "User-Agent": _USER_AGENT}
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            cookies=cookie_dict,
            headers=headers,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content

    def _resolve_direct_media_url(self, page_url: str) -> str | None:
        cdn_url, _ = self._navigate_and_capture(page_url)
        return cdn_url

    def _navigate_and_capture(self, page_url: str) -> tuple[str | None, dict | None]:
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
                    if any(h in url for h in _VIDEO_CDN_HOSTS) and not any(h in url for h in _EFFECT_CDN_HOSTS):
                        cdn_url = url
                if detail is None and _DETAIL_URL_FRAGMENT in url and "json" in ctype:
                    body = response.body().decode("utf-8", "replace")
                    data = json.loads(body)
                    aw = data.get("aweme_detail") if isinstance(data, dict) else None
                    if aw and "statistics" in aw:
                        detail = aw
            except Exception:
                # A response may arrive as the page tears down, or the body may
                # still be streaming; never let a handler error abort navigation.
                pass

        page: Page = self._browser.new_page()
        page.on("response", on_response)
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=self._timeout * 1000)
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


def _is_direct_media_url(url: str) -> bool:
    return url.endswith(".mp4") or any(h in url for h in _VIDEO_CDN_HOSTS)


def _aweme_to_meta(cdn_url: str | None, aw: dict | None) -> VideoMeta:
    aw = aw or {}
    stats = aw.get("statistics") or {}
    tags = [t.get("hashtag_name") for t in (aw.get("text_extra") or []) if t.get("hashtag_name")]
    duration_ms = aw.get("duration")
    create_time = aw.get("create_time")
    cover_url_list = ((aw.get("video") or {}).get("cover") or {}).get("url_list") or []
    desc = aw.get("desc") or ""
    return VideoMeta(
        cdn_url=cdn_url,
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
