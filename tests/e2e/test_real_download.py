"""End-to-end download test against real Douyin.

Marked ``e2e`` (skipped by default; run with ``uv run pytest -m e2e -s``).
Requires a logged-in browser profile (``uv run creator-agent auth-login``).

Verifies the full path: fetch metadata (title/description/tags/likes) + resolve
the CDN URL in one navigation, save a rich ``metadata.json``, then httpx-download
the complete mp4 using the resolved URL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.downloader.downloader import Downloader
from creator_agent.models.creator import Creator
from creator_agent.models.video import Video, VideoStats, VideoStatus
from creator_agent.storage.file_storage import FileStorage

CREATOR_URL = "https://www.douyin.com/user/MS4wLjABAAAAKsgyMHZwxugSUbpal0rg17wxX8A8ba350ld-N4oa79Y"
PROFILE = Path("./storage/.browser_profile")


@pytest.mark.e2e
def test_real_douyin_download_saves_video_and_metadata(tmp_path):
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

        # Grab any /video/{vid} link from the creator homepage.
        page = browser.new_page()
        page.goto(CREATOR_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        href = page.evaluate("document.querySelector('a[href*=\"/video/\"]')?.href")
        page.close()
        assert href, "no /video/ link found on creator homepage"

        # 1) Fetch metadata + resolve CDN URL.
        meta = downloader.fetch_video_meta(href)
        assert meta.cdn_url, "CDN URL was not resolved"
        assert meta.title, "title was not captured"
        assert meta.likes > 0, f"likes not captured (got {meta.likes})"
        assert meta.tags, "tags not captured"

        # 2) Build the Video and save metadata.json.
        video = Video(
            id="douyin_e2e_1",
            creator_id=creator.id,
            platform="douyin",
            platform_vid="1",
            title=meta.title,
            description=meta.description,
            cover_url=meta.cover_url,
            video_url=href,
            published_at=meta.published_at or datetime.now(UTC),
            duration_sec=meta.duration_sec,
            stats=VideoStats(
                likes=meta.likes,
                comments=meta.comments,
                favorites=meta.favorites,
                shares=meta.shares,
                views=meta.views,
            ),
            tags=meta.tags,
            collected_at=datetime.now(UTC),
            status=VideoStatus.NEW,
        )
        meta_path = downloader.save_metadata(creator, video)
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        assert saved["title"] == meta.title
        assert saved["description"] == meta.description
        assert saved["tags"] == meta.tags
        assert saved["stats"]["likes"] == meta.likes

        # 3) Download the complete mp4 via the pre-resolved CDN URL.
        path = downloader.download_video(creator, video, direct_url=meta.cdn_url)
        data = path.read_bytes()
        assert len(data) > 200_000, f"file too small ({len(data)} bytes)"
        assert data[4:8] == b"ftyp", f"not a valid mp4 (head={data[:8].hex()})"

        print(f"\n[e2e] mp4: {len(data)} bytes; meta: title={meta.title!r} likes={meta.likes} tags={meta.tags}")
    finally:
        browser.close()
