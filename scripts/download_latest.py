"""Download the latest video from a Douyin creator homepage.

Uses the logged-in browser profile + the Downloader. The creator's homepage is
scraped only for the first ``/video/`` link (the collector's card selector is
still broken, so this bypasses collection). For the chosen video it then:
  1. fetches metadata (title/description/tags/likes/...) + resolves the CDN URL
     in a single browser navigation,
  2. saves a rich ``metadata.json``,
  3. downloads the complete mp4 using the resolved CDN URL (no re-navigation).

Usage:
    uv run python scripts/download_latest.py <creator_homepage_url>
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Make Chinese title/tags print correctly on the Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.config import load_settings
from creator_agent.downloader.downloader import Downloader
from creator_agent.models.creator import Creator
from creator_agent.models.video import Video, VideoStats, VideoStatus
from creator_agent.storage.file_storage import FileStorage

_UID_RE = re.compile(r"/user/([^/?]+)")


def _extract_video_links(page) -> list[dict]:
    js = r"""() => {
        const seen = new Set();
        const out = [];
        for (const a of document.querySelectorAll('a[href*="/video/"]')) {
            const m = a.href.match(/\/video\/(\d+)/);
            if (!m || seen.has(m[1])) continue;
            seen.add(m[1]);
            out.push({href: a.href, vid: m[1], title: (a.textContent || '').trim().slice(0, 100)});
        }
        return out;
    }"""
    return page.evaluate(js)


def main(url: str) -> None:
    settings = load_settings()
    storage = FileStorage(settings.storage_dir)
    browser = BrowserManager(BrowserConfig(user_data_dir=settings.browser.user_data_dir, headless=True))
    downloader = Downloader(storage=storage, browser=browser, timeout_sec=90, retries=2)

    uid_match = _UID_RE.search(url)
    if not uid_match:
        print(f"Could not parse creator UID from URL: {url}")
        sys.exit(1)
    uid = uid_match.group(1)
    creator = Creator(
        id=f"douyin_{uid}",
        platform="douyin",
        platform_uid=uid,
        nickname="download-target",
        homepage_url=url,
        added_at=datetime.now(UTC),
    )

    browser.start()
    try:
        page = browser.new_page()
        print(f"Opening creator homepage: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)
        links = _extract_video_links(page)
        if not links:
            for _ in range(5):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(1500)
            links = _extract_video_links(page)
        page.close()

        if not links:
            print("No video links found on creator homepage (page may require login or changed layout).")
            return

        latest = links[0]
        print(f"Found {len(links)} video(s). Picking the first (top of page):")
        print(f"  vid   : {latest['vid']}")
        print(f"  url   : {latest['href']}")

        # 1) Fetch metadata + resolve CDN URL in one navigation.
        meta = downloader.fetch_video_meta(latest["href"])
        print("\nFetched metadata:")
        print(f"  title    : {meta.title!r}")
        print(f"  desc     : {meta.description[:120]!r}")
        print(f"  tags     : {meta.tags}")
        print(f"  likes    : {meta.likes}")
        print(f"  comments : {meta.comments}  shares: {meta.shares}  favorites: {meta.favorites}")
        print(f"  duration : {meta.duration_sec}s   published: {meta.published_at}")
        print(f"  cover    : {meta.cover_url}")
        print(f"  cdn_url  : {'resolved' if meta.cdn_url else 'NOT resolved'}")

        # 2) Build the Video with the rich metadata (keep the stable page URL).
        video = Video(
            id=f"douyin_{latest['vid']}",
            creator_id=creator.id,
            platform="douyin",
            platform_vid=latest["vid"],
            title=meta.title or latest["title"] or "untitled",
            description=meta.description,
            cover_url=meta.cover_url,
            video_url=latest["href"],
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
        print(f"\nMetadata saved: {Path(meta_path).resolve()}")

        # 3) Download the complete mp4 using the pre-resolved CDN URL.
        path = downloader.download_video(creator, video, direct_url=meta.cdn_url)
        data = path.read_bytes()
        print(f"\nVideo saved: {Path(path).resolve()}")
        print(f"  size      : {len(data):,} bytes ({len(data) / 1024 / 1024:.1f} MB)")
        print(f"  valid mp4 : {data[4:8] == b'ftyp'}  (head={data[:8].hex()})")

        print("\nffprobe:")
        try:
            r = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration,size:stream=codec_name,codec_type,width,height",
                    "-of",
                    "default=noprint_wrappers=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            print(r.stdout.strip() or "(no output)")
            if r.stderr.strip():
                print("stderr:", r.stderr.strip())
        except FileNotFoundError:
            print("  (ffprobe not installed; skipped)")
    finally:
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
