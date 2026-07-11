"""End-to-end download test against real Douyin.

Marked ``e2e`` (skipped by default; run with ``uv run pytest -m e2e -s``).
Requires a logged-in browser profile (``uv run creator-agent auth-login``).

Verifies the full download path: the Downloader is given a video *page* URL
(``video_url`` as produced by the collector), resolves the direct ``douyinvod``
CDN URL via Playwright response interception, then httpx-downloads the complete
file with cookies + Referer + UA and saves a valid mp4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.downloader.downloader import Downloader
from creator_agent.models.creator import Creator
from creator_agent.models.video import Video, VideoStatus
from creator_agent.storage.file_storage import FileStorage

CREATOR_URL = "https://www.douyin.com/user/MS4wLjABAAAAKsgyMHZwxugSUbpal0rg17wxX8A8ba350ld-N4oa79Y"
PROFILE = Path("./storage/.browser_profile")


@pytest.mark.e2e
def test_real_douyin_download_produces_valid_mp4(tmp_path):
    if not PROFILE.exists():
        pytest.skip("no logged-in browser profile; run `uv run creator-agent auth-login`")

    storage = FileStorage(tmp_path / "storage")
    browser = BrowserManager(BrowserConfig(user_data_dir=str(PROFILE), headless=True))
    downloader = Downloader(storage=storage, browser=browser, timeout_sec=60, retries=2)

    creator = Creator(
        id="douyin_e2e",
        platform="douyin",
        platform_uid="e2e",
        nickname="E2E",
        homepage_url=CREATOR_URL,
        added_at=datetime.now(UTC),
    )

    try:
        browser.start()

        # Grab any /video/{vid} link from the creator homepage. We deliberately
        # do NOT rely on the collector's card selector (which times out); any
        # /video/ anchor is enough to exercise the Downloader.
        page = browser.new_page()
        page.goto(CREATOR_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        href = page.evaluate("document.querySelector('a[href*=\"/video/\"]')?.href")
        page.close()
        assert href, "no /video/ link found on creator homepage"

        video = Video(
            id="douyin_e2e_1",
            creator_id=creator.id,
            platform="douyin",
            platform_vid="1",
            title="e2e",
            video_url=href,
            published_at=datetime.now(UTC),
            collected_at=datetime.now(UTC),
            status=VideoStatus.NEW,
        )

        path = downloader.download_video(creator, video)
        data = path.read_bytes()

        # A valid mp4 has an ftyp box at offset 4: bytes[4:8] == b"ftyp".
        # The previous (broken) intercept saved a ~200 KB partial fragment with
        # no ftyp header; a real video is a complete, multi-MB mp4.
        assert len(data) > 200_000, f"file too small ({len(data)} bytes) - likely a partial fragment"
        assert data[4:8] == b"ftyp", f"not a valid mp4 (head={data[:8].hex()})"
        print(f"\n[e2e] downloaded valid mp4: {len(data)} bytes -> {path}")
    finally:
        browser.close()
