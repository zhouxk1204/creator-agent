"""Download the latest video from a Douyin creator homepage for a given day.

Defaults to **today**; pass ``--date YYYY-MM-DD`` for a specific calendar day.
If the creator posted nothing that day, prints a notice and exits 0.

Uses the logged-in browser profile + the Downloader. The creator's homepage is
scraped for ``/video/`` links (newest first); for each candidate it fetches
metadata (title/description/tags/likes/... + published_at) + resolves the CDN
URL in a single browser navigation, picks the first video whose ``published_at``
falls in the target day, then:
  1. saves a rich ``metadata.json``,
  2. downloads the complete mp4 using the resolved CDN URL (no re-navigation).

Stops searching after ``OUT_OF_WINDOW_PAGE_THRESHOLD`` consecutive videos older
than the target day, so it won't walk the entire backlog.

Usage:
    uv run python scripts/download_latest.py <creator_homepage_url>              # today
    uv run python scripts/download_latest.py <url> --date 2026-07-11
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make Chinese title/tags print correctly on the Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.collector.base import day_filter, today_filter
from creator_agent.collector.douyin.collector import OUT_OF_WINDOW_PAGE_THRESHOLD
from creator_agent.collector.douyin.parser import collect_video_links
from creator_agent.config import load_settings
from creator_agent.downloader.downloader import Downloader
from creator_agent.models.creator import Creator
from creator_agent.models.video import Video, VideoStats, VideoStatus
from creator_agent.storage.file_storage import FileStorage

_UID_RE = re.compile(r"/user/([^/?]+)")


def _resolve_window(date_str: str | None) -> tuple[str, object]:
    if date_str:
        try:
            target_day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid date '{date_str}', expected YYYY-MM-DD (e.g. 2026-07-11)")
            sys.exit(2)
        return target_day.isoformat(), day_filter(target_day)
    return "today", today_filter()


def main(url: str, date_str: str | None) -> None:
    if not url:
        print("Usage: download_latest.py <creator_homepage_url> [--date YYYY-MM-DD]")
        sys.exit(2)

    window_label, filter_obj = _resolve_window(date_str)

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
        added_at=datetime.now(timezone.utc),
    )

    browser.start()
    try:
        page = browser.new_page()
        print(f"Opening creator homepage: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)

        # Scroll a bit to load the video grid, then collect links (newest first).
        links = collect_video_links(page)
        if not links:
            for _ in range(5):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(1500)
            links = collect_video_links(page)
        page.close()

        if not links:
            print("No video links found on creator homepage (page may require login or changed layout).")
            return

        print(f"Found {len(links)} video link(s). Searching for {window_label}...")

        chosen_meta = None
        chosen_link = None
        consecutive_out_of_window = 0
        for link in links:
            meta = downloader.fetch_video_meta(link["href"])
            published = meta.published_at
            if published is not None and published >= filter_obj.end:
                continue  # newer than the target day; keep looking
            if published is not None and published < filter_obj.start:
                consecutive_out_of_window += 1
                if consecutive_out_of_window >= OUT_OF_WINDOW_PAGE_THRESHOLD:
                    print(f"Stopped after {consecutive_out_of_window} consecutive videos older than {window_label}.")
                    break
                continue
            # In window (or published_at unknown) - newest in-window video found.
            chosen_meta = meta
            chosen_link = link
            break

        if chosen_meta is None or chosen_link is None:
            print(f"No video found for {window_label}.")
            return

        print(f"Selected video {chosen_link['vid']} (published: {chosen_meta.published_at}):")
        print(f"  title    : {chosen_meta.title!r}")
        print(f"  desc     : {chosen_meta.description[:120]!r}")
        print(f"  tags     : {chosen_meta.tags}")
        print(f"  likes    : {chosen_meta.likes}")
        print(f"  comments : {chosen_meta.comments}  shares: {chosen_meta.shares}  favorites: {chosen_meta.favorites}")
        print(f"  duration : {chosen_meta.duration_sec}s   cover: {chosen_meta.cover_url}")
        print(f"  cdn_url  : {'resolved' if chosen_meta.cdn_url else 'NOT resolved'}")

        video = Video(
            id=f"douyin_{chosen_link['vid']}",
            creator_id=creator.id,
            platform="douyin",
            platform_vid=chosen_link["vid"],
            title=chosen_meta.title or chosen_link.get("title", "") or "untitled",
            description=chosen_meta.description,
            cover_url=chosen_meta.cover_url,
            video_url=chosen_link["href"],
            published_at=chosen_meta.published_at or datetime.now(timezone.utc),
            duration_sec=chosen_meta.duration_sec,
            stats=VideoStats(
                likes=chosen_meta.likes,
                comments=chosen_meta.comments,
                favorites=chosen_meta.favorites,
                shares=chosen_meta.shares,
                views=chosen_meta.views,
            ),
            tags=chosen_meta.tags,
            collected_at=datetime.now(timezone.utc),
            status=VideoStatus.NEW,
        )

        meta_path = downloader.save_metadata(creator, video)
        print(f"\nMetadata saved: {Path(meta_path).resolve()}")

        path = downloader.download_video(creator, video, direct_url=chosen_meta.cdn_url)
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
                    "default=noprintwrappers=1",
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
    parser = argparse.ArgumentParser(description="Download the latest Douyin video for a given day.")
    parser.add_argument("url", help="Creator homepage URL")
    parser.add_argument("--date", help="Target calendar day (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    main(args.url, args.date)
