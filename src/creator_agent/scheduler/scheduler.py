from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from creator_agent.collector.base import CollectFilter, yesterday_filter

if TYPE_CHECKING:
    from creator_agent.pipeline.runner import PipelineRunner
    from creator_agent.repository.sqlite_repo import Repository

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, runner: PipelineRunner, repo: Repository) -> None:
        self._runner = runner
        self._repo = repo

    def run_once(self, filter: CollectFilter | None = None) -> int:
        f = filter or yesterday_filter()
        creators = self._repo.list_creators()

        if not creators:
            logger.info("No creators configured. Add one with creator-agent creator add.")
            return 0

        logger.info(
            "Starting sync for %d creators (filter: %s ~ %s)",
            len(creators),
            f.start.isoformat(),
            f.end.isoformat(),
        )

        synced_count = 0
        for creator in creators:
            try:
                self._runner.sync_creator(creator, f)
                synced_count += 1
            except Exception as e:
                logger.exception("Creator %s (%s) sync failed: %s", creator.id, creator.nickname, e)
                self._repo.update_creator_sync(creator.id, "failed", f.start, str(e))

        logger.info("Sync round complete: %d/%d creators processed", synced_count, len(creators))
        return synced_count
