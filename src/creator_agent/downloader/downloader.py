from __future__ import annotations

import logging
import time
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

    def download_video(self, creator: Creator, video: Video) -> Path:
        if not video.video_url:
            raise ValueError(f"Video {video.id} has no video_url")

        url = str(video.video_url)
        logger.info("Downloading video: %s", url)

        # ``video_url`` from the Douyin collector is the video *page* URL, not a
        # direct media URL. Resolve the real CDN URL via the browser first.
        direct_url = url
        if not _is_direct_media_url(url):
            logger.info("video_url is a page URL; resolving direct CDN URL via browser...")
            direct_url = self._resolve_direct_media_url(url)
            if not direct_url:
                raise RuntimeError(f"Could not resolve a direct video URL for video {video.id} from {url}")
            logger.info("Resolved direct URL: %s", direct_url)

        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                data = self._httpx_get(direct_url, referer=_DOUYIN_REFERER)
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
        """Navigate to a Douyin video page and capture the direct CDN URL of the
        main video mp4 (skipping effect overlays).

        Returns the URL, or None if no media response was seen. Only response
        metadata (url/headers) is read here -- ``response.body()`` is deliberately
        avoided because Douyin serves the video via byte-range requests and the
        body captured from a single response is a partial/corrupt fragment. The
        full file is fetched separately via :meth:`_httpx_get`.
        """
        captured: list[str] = []

        def on_response(response) -> None:
            try:
                ctype = response.headers.get("content-type", "")
                url = response.url
                if "video/mp4" not in ctype and not url.endswith(".mp4"):
                    return
                if any(h in url for h in _EFFECT_CDN_HOSTS):
                    return
                # Prefer the real video CDN; keep the first other mp4 as a fallback.
                if any(h in url for h in _VIDEO_CDN_HOSTS):
                    captured.insert(0, url)
                elif not captured:
                    captured.append(url)
            except Exception:
                # A response may arrive as the page tears down; never let a
                # handler error abort the navigation.
                pass

        page: Page = self._browser.new_page()
        page.on("response", on_response)
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=self._timeout * 1000)
            page.wait_for_timeout(5000)
            if not captured:
                # The <video> element may be lazy; kick the player so it requests
                # the media stream.
                try:
                    page.evaluate('document.querySelector("video")?.play()')
                except Exception:
                    pass
                page.wait_for_timeout(5000)
        finally:
            page.close()

        return captured[0] if captured else None


def _is_direct_media_url(url: str) -> bool:
    return url.endswith(".mp4") or any(h in url for h in _VIDEO_CDN_HOSTS)
