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

        for attempt in range(self._retries):
            try:
                cookies = self._browser.get_cookies("www.douyin.com")
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                with httpx.Client(timeout=self._timeout, follow_redirects=True, cookies=cookie_dict) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.content
                path = self._storage.save_video_file(creator.id, video.id, data)
                logger.info("Video saved: %s", path)
                return path
            except Exception as e:
                logger.warning("httpx attempt %d/%d failed: %s", attempt + 1, self._retries, e)
                if attempt < self._retries - 1:
                    time.sleep(2**attempt)

        logger.info("Falling back to Playwright download for video: %s", video.id)
        return self._download_via_playwright(creator, video, url)

    def download_cover(self, creator: Creator, video: Video) -> Path | None:
        if not video.cover_url:
            logger.info("No cover_url for video %s, skipping.", video.id)
            return None
        url = str(video.cover_url)
        logger.info("Downloading cover: %s", url)
        for attempt in range(self._retries):
            try:
                with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.content
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

    def _download_via_playwright(self, creator: Creator, video: Video, url: str) -> Path:
        data: bytes | None = None

        def handle_response(response) -> None:
            nonlocal data
            ctype = response.headers.get("content-type", "")
            if "video/mp4" in ctype or response.url.endswith(".mp4"):
                data = response.body()

        page: Page = self._browser.new_page()
        page.on("response", handle_response)

        try:
            page.goto(url, wait_until="load", timeout=self._timeout * 1000)
            page.wait_for_timeout(5000)
            if data is None:
                page.evaluate('document.querySelector("video")?.play()')
                page.wait_for_timeout(3000)
            if data:
                path = self._storage.save_video_file(creator.id, video.id, data)
                logger.info("Video saved via Playwright: %s", path)
                return path
            else:
                raise RuntimeError("Playwright intercept did not capture any video data")
        finally:
            page.close()
