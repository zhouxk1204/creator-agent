from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from creator_agent.collector.base import CollectFilter, today_filter
from creator_agent.collector.douyin.collector import DouyinCollector
from creator_agent.collector.douyin.meta import aweme_to_meta, navigate_and_capture
from creator_agent.collector.douyin.url import parse_video_target
from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo, VideoStats, VideoStatus

if TYPE_CHECKING:
    from creator_agent.asr.transcriber import Transcriber
    from creator_agent.browser.manager import BrowserManager
    from creator_agent.downloader.downloader import Downloader
    from creator_agent.repository.sqlite_repo import Repository
    from creator_agent.storage.file_storage import FileStorage

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    collected: int = 0
    downloaded: int = 0
    transcribed: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class SingleVideoResult:
    """Outcome of :meth:`PipelineRunner.sync_video_url` (one pasted URL)."""

    video_id: str = ""
    creator_id: str = ""
    creator_nickname: str = ""
    title: str = ""
    downloaded: bool = False
    transcribed: bool = False
    error: str | None = None


class PipelineRunner:
    def __init__(
        self,
        repo: Repository,
        storage: FileStorage,
        browser: BrowserManager,
        downloader: Downloader,
        transcriber: Transcriber | None = None,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._browser = browser
        self._downloader = downloader
        self._transcriber = transcriber
        self._collector = DouyinCollector()

    def sync_creator(
        self,
        creator: Creator,
        filter: CollectFilter | None = None,
        do_asr: bool = False,
    ) -> SyncResult:
        filter = filter or today_filter()
        logger.info(
            "Syncing creator %s (%s) - filter: %s ~ %s",
            creator.id,
            creator.nickname,
            filter.start.isoformat(),
            filter.end.isoformat(),
        )

        self._repo.update_creator_sync(creator.id, "syncing", filter.start)
        sync_id = self._repo.start_sync(creator.id)

        try:
            collected = self._collector.collect(creator, filter, self._browser)
        except Exception as e:
            logger.exception("Collect failed for creator %s: %s", creator.id, e)
            self._repo.update_creator_sync(creator.id, "failed", filter.start, str(e))
            self._repo.finish_sync(sync_id, "failed", 0, 0, str(e))
            return SyncResult()

        logger.info("Collected %d videos for %s", len(collected), creator.id)

        downloaded_count = 0
        failed: list[tuple[str, str]] = []

        for cv in collected:
            video = self._repo.upsert_collected(creator, cv)
            if video.status == VideoStatus.VIDEO_DOWNLOADED:
                logger.debug("Video %s already VIDEO_DOWNLOADED, skipping.", video.id)
                continue

            try:
                if video.status == VideoStatus.NEW:
                    self._downloader.save_metadata(creator, video)
                    self._repo.advance_status(video.id, VideoStatus.METADATA_SAVED)
                    logger.info("Video %s -> METADATA_SAVED", video.id)

                if video.status in (VideoStatus.NEW, VideoStatus.METADATA_SAVED):
                    self._downloader.download_video(creator, video)
                    self._downloader.download_cover(creator, video)
                    self._repo.advance_status(video.id, VideoStatus.VIDEO_DOWNLOADED)
                    downloaded_count += 1
                    logger.info("Video %s -> VIDEO_DOWNLOADED", video.id)

            except Exception as e:
                logger.error("Video %s processing failed: %s", video.id, e)
                failed.append((video.id, str(e)))

        # ASR stage (optional): transcribe every VIDEO_DOWNLOADED video for this
        # creator that doesn't already have a transcript. Runs before finish_sync
        # so sync_history captures the full run; per-video failures are non-fatal.
        transcribed = 0
        if do_asr:
            if self._transcriber is None:
                logger.warning("ASR requested but transcriber not configured; skipping.")
            else:
                candidates = self._asr_candidates(creator)
                if candidates:
                    logger.info("ASR stage: %d candidate(s) for %s", len(candidates), creator.id)
                    transcribed, asr_failed = self._transcriber.transcribe_batch(creator, candidates)
                    failed.extend(asr_failed)
                else:
                    logger.info("ASR stage: no pending videos for %s", creator.id)

        overall_status = "ok" if not failed else "partial"
        self._repo.update_creator_sync(creator.id, overall_status, filter.start)
        self._repo.finish_sync(
            sync_id,
            overall_status,
            len(collected),
            downloaded_count,
            error="\n".join(f"{vid}: {err}" for vid, err in failed) or None,
        )

        result = SyncResult(
            collected=len(collected),
            downloaded=downloaded_count,
            transcribed=transcribed,
            failed=failed,
        )
        logger.info(
            "Sync complete for %s: %d collected, %d downloaded, %d transcribed, %d failed",
            creator.id,
            result.collected,
            result.downloaded,
            result.transcribed,
            len(result.failed),
        )
        return result

    def sync_video_url(
        self,
        raw: str,
        do_asr: bool = True,
        timeout_sec: int = 120,
        progress: Callable[[str], None] | None = None,
    ) -> SingleVideoResult:
        """Run the full pipeline for one pasted Douyin URL / share text / id.

        Same state machine as :meth:`sync_creator` (NEW -> METADATA_SAVED ->
        VIDEO_DOWNLOADED -> ASR_DONE), just entered with a single video instead
        of a creator window. The creator is auto-registered from the aweme
        detail's author if not already known, so the result shows up in the
        web UI and is resumable like any synced video.

        ``progress`` (if given) is called with a short human-readable message
        at each stage boundary - the CLI prints them, the web UI lists them.
        """

        def stage(msg: str) -> None:
            logger.info("%s", msg)
            if progress:
                progress(msg)

        result = SingleVideoResult()
        try:
            platform_vid, page_url = parse_video_target(raw, timeout_sec=min(timeout_sec, 30))
            result.video_id = f"douyin_{platform_vid}"
            stage(f"解析链接 -> 视频 {platform_vid}")

            stage("打开视频页，解析元数据与下载地址…")
            cdn_url, detail = navigate_and_capture(page_url, self._browser, timeout_sec)
            meta = aweme_to_meta(cdn_url, detail)
            if not meta.cdn_url:
                raise RuntimeError("未能解析到视频下载地址 (CDN URL)")
            result.title = meta.title

            creator = self._ensure_author_creator(detail, platform_vid)
            result.creator_id = creator.id
            result.creator_nickname = creator.nickname
            stage(f"创作者: {creator.nickname} ({creator.id})")

            cv = CollectedVideo(
                platform="douyin",
                platform_vid=platform_vid,
                title=meta.title or platform_vid,
                description=meta.description,
                # Store the *page* URL (stable); the expiring CDN URL is passed
                # to the downloader directly below.
                video_url=page_url,  # type: ignore[arg-type]
                cover_url=meta.cover_url,  # type: ignore[arg-type]
                published_at=meta.published_at or datetime.now(UTC),
                stats=VideoStats(
                    likes=meta.likes,
                    comments=meta.comments,
                    favorites=meta.favorites,
                    shares=meta.shares,
                    views=meta.views,
                ),
                hashtags=meta.tags,
            )
            video = self._repo.upsert_collected(creator, cv)

            if video.status == VideoStatus.NEW:
                self._downloader.save_metadata(creator, video)
                self._repo.advance_status(video.id, VideoStatus.METADATA_SAVED)
                video = self._repo.get_video(video.id) or video

            if video.status == VideoStatus.METADATA_SAVED:
                stage("下载视频与封面…")
                self._downloader.download_video(creator, video, direct_url=meta.cdn_url)
                self._downloader.download_cover(creator, video)
                self._repo.advance_status(video.id, VideoStatus.VIDEO_DOWNLOADED)
                video = self._repo.get_video(video.id) or video
                result.downloaded = True
                stage("下载完成")
            elif video.status in (VideoStatus.VIDEO_DOWNLOADED, VideoStatus.ASR_DONE):
                # Already downloaded by an earlier run - nothing to redo.
                result.downloaded = True
                stage("视频已下载（跳过）")

            if do_asr and video.status == VideoStatus.VIDEO_DOWNLOADED:
                if self._transcriber is None:
                    stage("ASR 未配置，跳过转写")
                elif self._storage.load_transcript(creator, video):
                    result.transcribed = True
                    stage("已有转写（跳过）")
                else:
                    stage("提取音频并转写（ASR，首次加载模型较慢）…")
                    count, failed = self._transcriber.transcribe_batch(creator, [video])
                    result.transcribed = count > 0
                    if failed:
                        raise RuntimeError(f"ASR 失败: {failed[0][1]}")
                    stage("转写完成")
        except Exception as e:
            logger.exception("sync_video_url failed for %r", raw)
            result.error = str(e)
        return result

    def _ensure_author_creator(self, detail: dict | None, platform_vid: str) -> Creator:
        """Return the creator row for the aweme's author, registering it if new.

        ``sec_uid`` is preferred as ``platform_uid`` so the id matches what
        ``creator add <homepage-url>`` produces (same person, same row).
        """
        author = (detail or {}).get("author") or {}
        sec_uid = author.get("sec_uid") or ""
        uid = author.get("uid") or ""
        nickname = author.get("nickname") or ""
        avatar_list = ((author.get("avatar_thumb") or {}).get("url_list")) or []

        platform_uid = sec_uid or uid or f"adhoc_{platform_vid}"
        creator_id = f"douyin_{platform_uid}"
        existing = self._repo.get_creator(creator_id)
        if existing:
            return existing

        creator = Creator(
            id=creator_id,
            platform="douyin",
            platform_uid=platform_uid,
            nickname=nickname or platform_uid,
            homepage_url=(f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "https://www.douyin.com/"),  # type: ignore[arg-type]
            avatar_url=avatar_list[0] if avatar_list else None,  # type: ignore[arg-type]
            added_at=datetime.now(UTC),
            sync_status="idle",
        )
        self._repo.add_creator(creator)
        logger.info("Auto-registered creator %s (%s) from pasted URL", creator.id, creator.nickname)
        return creator

    def run_asr(self, creator: Creator, limit: int | None = None) -> SyncResult:
        """Transcribe pending videos for one creator (standalone ``asr`` command).

        Selects videos at ``VIDEO_DOWNLOADED`` without an existing transcript and
        runs the ASR batch. Resumable: ``ASR_DONE`` videos are excluded by status.
        """
        if self._transcriber is None:
            raise RuntimeError(
                "ASR is not configured. Set asr.env_python (and optionally asr.enabled) "
                "in config/settings.yaml to point at the dedicated creator-asr env."
            )
        candidates = self._asr_candidates(creator, limit)
        if not candidates:
            logger.info("No pending ASR videos for %s.", creator.id)
            return SyncResult()
        logger.info("ASR: %d candidate(s) for %s", len(candidates), creator.id)
        transcribed, failed = self._transcriber.transcribe_batch(creator, candidates)
        result = SyncResult(transcribed=transcribed, failed=failed)
        logger.info(
            "ASR complete for %s: %d transcribed, %d failed",
            creator.id,
            result.transcribed,
            len(result.failed),
        )
        return result

    def _asr_candidates(self, creator: Creator, limit: int | None = None) -> list:
        """Videos at VIDEO_DOWNLOADED for this creator, minus any with a transcript."""
        rows = self._repo.list_videos_by_status([VideoStatus.VIDEO_DOWNLOADED])
        pending = [v for v in rows if v.creator_id == creator.id and not self._storage.load_transcript(creator, v)]
        if limit is not None:
            pending = pending[:limit]
        return pending
