from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from creator_agent.collector.douyin.meta import (
    DOUYIN_REFERER,
    USER_AGENT,
    VideoMeta,
    fetch_video_meta,
    is_direct_media_url,
)
from creator_agent.models.creator import Creator
from creator_agent.models.video import Video

if TYPE_CHECKING:
    from creator_agent.browser.manager import BrowserManager
    from creator_agent.storage.file_storage import FileStorage

logger = logging.getLogger(__name__)

_DOWNLOAD_RETRIES = 3
_DOWNLOAD_TIMEOUT_S = 120

__all__ = ["Downloader", "VideoMeta"]


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
            if not is_direct_media_url(url):
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
                data = self._httpx_get(url, referer=DOUYIN_REFERER)
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
                data = self._httpx_get(url, referer=DOUYIN_REFERER)
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
        """Navigate a Douyin video page once; resolve the direct CDN URL and
        rich metadata (title/description/tags/stats/duration/cover/published_at).

        Delegates to the shared :func:`creator_agent.collector.douyin.meta.fetch_video_meta`.
        """
        return fetch_video_meta(page_url, self._browser, self._timeout)

    # -- internals ----------------------------------------------------------

    def _httpx_get(self, url: str, referer: str) -> bytes:
        cookies = self._browser.get_cookies("www.douyin.com")
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        headers = {"Referer": referer, "User-Agent": USER_AGENT}
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
        meta = fetch_video_meta(page_url, self._browser, self._timeout)
        return meta.cdn_url
