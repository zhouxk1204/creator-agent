"""Download a single Douyin video and generate subtitles (ASR -> SRT).

Usage:
    uv run python scripts/extract_video.py "https://www.douyin.com/video/7664966551623322914"
    uv run python scripts/extract_video.py "https://www.douyin.com/user/xxx?modal_id=7664966551623322914"
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from creator_agent.asr.transcriber import Transcriber
from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.collector.douyin.meta import aweme_to_meta, navigate_and_capture
from creator_agent.config import load_settings
from creator_agent.downloader.downloader import Downloader
from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo, VideoStats, VideoStatus
from creator_agent.repository.sqlite_repo import Repository
from creator_agent.storage.file_storage import FileStorage

_RE_VID = re.compile(r"/video/(\d+)")


def _find_video_id(url: str) -> str:
    m = _RE_VID.search(url)
    if m:
        return m.group(1)
    m = re.search(r"modal_id=(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"vid=(\d+)", url)
    if m:
        return m.group(1)
    raise SystemExit(f"Could not parse video ID from: {url}")


def main(url: str) -> None:
    vid = _find_video_id(url)
    video_url = f"https://www.douyin.com/video/{vid}"

    settings = load_settings()
    storage = FileStorage(settings.storage_dir)
    repo = Repository(settings.db_path)
    browser = BrowserManager(BrowserConfig(user_data_dir=settings.browser.user_data_dir, headless=True))
    downloader = Downloader(storage=storage, browser=browser, timeout_sec=120, retries=3)

    browser.start()
    try:
        print(f"Fetching video metadata: {video_url}")
        cdn_url, detail = navigate_and_capture(video_url, browser, 120)
        meta = aweme_to_meta(cdn_url, detail)

        if detail:
            author = detail.get("author") or {}
            nickname = (author.get("nickname") or f"douyin_{vid}").strip()
            uid = author.get("sec_uid") or "unknown"
            bio = author.get("signature") or ""
            avatar_list = (author.get("avatar_thumb") or {}).get("url_list") or []
            avatar_url = avatar_list[0].replace("\\u0026", "&") if avatar_list else None
        else:
            nickname, uid, bio, avatar_url = f"douyin_{vid}", "unknown", "", None

        creator = Creator(
            id=f"douyin_{uid}",
            platform="douyin",
            platform_uid=uid,
            nickname=nickname,
            homepage_url=f"https://www.douyin.com/user/{uid}",
            avatar_url=avatar_url,
            bio=bio,
            added_at=datetime.now(timezone.utc),
        )
        existing = repo.get_creator(creator.id)
        if not existing:
            repo.add_creator(creator)
        else:
            creator = existing

        print(f"Creator : {creator.nickname} ({creator.id})")
        print(f"Title   : {meta.title!r}")
        if meta.duration_sec:
            print(f"Duration: {meta.duration_sec}s")
        print(f"CDN URL : {'resolved' if meta.cdn_url else 'NOT resolved'}")

        cv = CollectedVideo(
            platform="douyin",
            platform_vid=vid,
            title=meta.title or f"video_{vid}",
            description=meta.description,
            video_url=str(meta.cdn_url) if meta.cdn_url else video_url,
            cover_url=meta.cover_url,
            published_at=meta.published_at or datetime.now(timezone.utc),
            stats=VideoStats(
                likes=meta.likes,
                comments=meta.comments,
                favorites=meta.favorites,
                shares=meta.shares,
                views=meta.views,
            ),
            hashtags=list(meta.tags),
        )
        video = repo.upsert_collected(creator, cv)
        print(f"Storage : {video.storage_path}")

        meta_path = downloader.save_metadata(creator, video)
        print(f"Metadata: {meta_path}")

        if video.status.value not in ("VIDEO_DOWNLOADED", "ASR_DONE"):
            path = downloader.download_video(creator, video, direct_url=meta.cdn_url)
            data = path.read_bytes()
            print(f"Video   : {path}")
            print(f"Size    : {len(data):,} bytes ({len(data) / 1024 / 1024:.1f} MB)")

            downloader.download_cover(creator, video)
            repo.advance_status(video.id, VideoStatus.VIDEO_DOWNLOADED)

        print("\nRunning ASR (FunASR paraformer-zh)... this may take a while on first run.")
        transcriber = Transcriber(storage=storage, settings=settings.asr, repo=repo)
        transcribed, failed = transcriber.transcribe_batch(creator, [video])
        if failed:
            for vid_id, err in failed:
                print(f"  ASR FAIL {vid_id}: {err}")
        if transcribed:
            t = storage.load_transcript(creator, video)
            folder = storage._video_path(creator, video)
            srt_path = folder / "transcript.srt"
            print(f"Transcript : {folder / 'transcript.json'}")
            print(f"SRT subtitle: {srt_path}")
            print(f"Chars      : {len(t.text)}")
            print("\n=== TRANSCRIPT ===")
            print(t.text)
            print("\n=== SRT (first 60 lines) ===")
            if srt_path.exists():
                lines = srt_path.read_text(encoding="utf-8").splitlines()
                print("\n".join(lines[:60]))
                if len(lines) > 60:
                    print(f"... ({len(lines)} lines total)")
    finally:
        browser.close()
        repo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download one Douyin video and generate SRT subtitles.")
    parser.add_argument("url", help="Douyin video URL or user URL with modal_id/vid")
    args = parser.parse_args()
    main(args.url)
