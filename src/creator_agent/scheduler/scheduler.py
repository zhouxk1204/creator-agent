from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from creator_agent.collector.base import CollectFilter, today_filter

if TYPE_CHECKING:
    from creator_agent.pipeline.runner import PipelineRunner, SyncResult
    from creator_agent.repository.sqlite_repo import Repository

logger = logging.getLogger(__name__)


@dataclass
class SchedulerRoundResult:
    creators_total: int = 0
    creators_processed: int = 0
    collected: int = 0
    downloaded: int = 0


class Scheduler:
    def __init__(self, runner: PipelineRunner, repo: Repository) -> None:
        self._runner = runner
        self._repo = repo

    def run_once(self, filter: CollectFilter | None = None) -> SchedulerRoundResult:
        f = filter or today_filter()
        creators = self._repo.list_creators()

        if not creators:
            logger.info("No creators configured. Add one with creator-agent creator add.")
            return SchedulerRoundResult()

        logger.info(
            "Starting sync for %d creators (filter: %s ~ %s)",
            len(creators),
            f.start.isoformat(),
            f.end.isoformat(),
        )

        result = SchedulerRoundResult(creators_total=len(creators))
        for creator in creators:
            try:
                sync_result: SyncResult = self._runner.sync_creator(creator, f)
                result.creators_processed += 1
                result.collected += sync_result.collected
                result.downloaded += sync_result.downloaded
            except Exception as e:
                logger.exception("Creator %s (%s) sync failed: %s", creator.id, creator.nickname, e)
                self._repo.update_creator_sync(creator.id, "failed", f.start, str(e))

        logger.info(
            "Sync round complete: %d/%d creators processed, %d collected, %d downloaded",
            result.creators_processed,
            result.creators_total,
            result.collected,
            result.downloaded,
        )
        return result
