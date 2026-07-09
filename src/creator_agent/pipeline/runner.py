from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from creator_agent.collector.base import CollectFilter, yesterday_filter
from creator_agent.collector.douyin.collector import DouyinCollector
from creator_agent.models.creator import Creator
from creator_agent.models.video import VideoStatus

if TYPE_CHECKING:
    from creator_agent.browser.manager import BrowserManager
    from creator_agent.downloader.downloader import Downloader
    from creator_agent.repository.sqlite_repo import Repository
    from creator_agent.storage.file_storage import FileStorage

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    collected: int = 0
    downloaded: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


class PipelineRunner:
    def __init__(
        self,
        repo: Repository,
        storage: FileStorage,
        browser: BrowserManager,
        downloader: Downloader,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._browser = browser
        self._downloader = downloader
        self._collector = DouyinCollector()

    def sync_creator(
        self,
        creator: Creator,
        filter: CollectFilter | None = None,
    ) -> SyncResult:
        filter = filter or yesterday_filter()
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

        overall_status = "ok" if not failed else "partial"
        self._repo.update_creator_sync(creator.id, overall_status, filter.start)
        self._repo.finish_sync(
            sync_id,
            overall_status,
            len(collected),
            downloaded_count,
            error="\n".join(f"{vid}: {err}" for vid, err in failed) or None,
        )

        result = SyncResult(collected=len(collected), downloaded=downloaded_count, failed=failed)
        logger.info(
            "Sync complete for %s: %d collected, %d downloaded, %d failed",
            creator.id,
            result.collected,
            result.downloaded,
            len(result.failed),
        )
        return result
