"""Refresh cover thumbnails for already-downloaded videos (Phase 2 utility).

The collector originally stored the aweme-detail ``video.cover`` URL, which can
resolve to a different processed image than the thumbnail shown on Douyin's
creator grid. The collector now scrapes the grid card ``<img>`` instead, but
already-downloaded videos keep their old (wrong) ``cover.jpg``. This script
re-scrapes the creator's profile grid once, maps every visible card to its
correct cover URL, and re-downloads ``cover.jpg`` for each existing video.

The web UI serves ``cover.jpg`` straight from disk, so this is all that's
needed for the displayed covers to flip to the correct image.

Usage:
    uv run python scripts/refresh_covers.py <creator_id>
    uv run python scripts/refresh_covers.py <creator_id> --max-scrolls 30
    uv run python scripts/refresh_covers.py <creator_id> --dry-run
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.collector.douyin.parser import collect_video_links
from creator_agent.config import load_settings
from creator_agent.downloader.downloader import Downloader
from creator_agent.repository.sqlite_repo import Repository
from creator_agent.storage.file_storage import FileStorage


def scrape_card_covers(page, max_scrolls: int) -> dict[str, str]:
    """Scroll the creator grid and collect ``{platform_vid: cover_url}``.

    Only cards whose ``<img>`` cover was captured are mapped; vids seen without
    a cover are tracked separately so we know when scrolling yields nothing new.
    """
    covers: dict[str, str] = {}
    seen: set[str] = set()
    no_new_streak = 0
    for _ in range(max_scrolls):
        new_with_cover = 0
        any_new = False
        for ln in collect_video_links(page):
            vid = ln["vid"]
            if vid in seen:
                continue
            seen.add(vid)
            any_new = True
            cover = (ln.get("cover") or "").strip()
            if cover:
                covers[vid] = cover
                new_with_cover += 1
        if any_new:
            no_new_streak = 0
        else:
            no_new_streak += 1
            if no_new_streak >= 3:
                break
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(600)
        time.sleep(random.uniform(0.6, 1.6))
    return covers


def main(creator_id: str, max_scrolls: int, dry_run: bool) -> None:
    settings = load_settings()
    repo = Repository(settings.db_path)
    storage = FileStorage(settings.storage_dir)
    browser = BrowserManager(BrowserConfig(user_data_dir=settings.browser.user_data_dir, headless=True))
    downloader = Downloader(storage=storage, browser=browser, timeout_sec=120, retries=3)
    try:
        creator = repo.get_creator(creator_id)
        if not creator:
            print(f"Creator not found: {creator_id}")
            sys.exit(1)

        videos = repo.list_videos(creator.id)
        if not videos:
            print(f"No videos for {creator.nickname} ({creator.id}).")
            return

        print(f"Loading grid for {creator.nickname} ({creator.id})...")
        browser.start()
        page = browser.new_page()
        try:
            page.goto(str(creator.homepage_url), wait_until="load", timeout=30000)
            page.wait_for_selector('a[href*="/video/"]', timeout=15000)
            covers = scrape_card_covers(page, max_scrolls)
        finally:
            page.close()
        print(f"Scraped {len(covers)} card covers from the grid.")

        refreshed = skipped = missing = 0
        for video in videos:
            new_cover = covers.get(video.platform_vid)
            if not new_cover:
                print(f"  SKIP {video.platform_vid}: no card cover on grid (off-screen or removed).")
                missing += 1
                continue
            if video.cover_url and str(video.cover_url) == new_cover:
                skipped += 1
                continue
            if dry_run:
                print(f"  DRY  {video.platform_vid}: would set {new_cover[:80]}")
                refreshed += 1
                continue
            # Overwrite cover.jpg (what the web UI displays).
            video.cover_url = new_cover  # download_cover reads str(cover_url)
            path = downloader.download_cover(creator, video)
            # Keep DB + metadata.json consistent with the new cover URL.
            repo.update_video_cover_url(video.id, new_cover)
            meta_path = storage._video_path(creator, video) / "metadata.json"  # noqa: SLF001
            if meta_path.exists():
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                data["cover_url"] = new_cover
                meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  OK   {video.platform_vid}: {path.name}")
            refreshed += 1

        action = "would refresh" if dry_run else "refreshed"
        print(f"\nDone: {refreshed} {action}, {skipped} already correct, {missing} no grid cover.")
    finally:
        browser.close()
        repo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh cover thumbnails for a creator's videos.")
    parser.add_argument("creator_id", help="Creator ID (e.g. douyin_MS4w...)")
    parser.add_argument("--max-scrolls", type=int, default=25, help="Max grid scrolls (default 25).")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing.")
    args = parser.parse_args()
    main(args.creator_id, args.max_scrolls, args.dry_run)
