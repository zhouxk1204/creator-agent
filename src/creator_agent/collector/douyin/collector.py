from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

from creator_agent.collector.base import CollectFilter, Collector
from creator_agent.collector.douyin.parser import (
    card_to_collected,
    parse_visible_cards,
)
from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo

if TYPE_CHECKING:
    from creator_agent.browser.manager import BrowserManager

logger = logging.getLogger(__name__)

OUT_OF_WINDOW_PAGE_THRESHOLD = 2


class DouyinCollector(Collector):
    platform: str = "douyin"

    def __init__(
        self,
        scroll_delay: tuple[float, float] = (1.0, 3.0),
        out_of_window_threshold: int = OUT_OF_WINDOW_PAGE_THRESHOLD,
    ) -> None:
        self._scroll_delay = scroll_delay
        self._out_of_window_threshold = out_of_window_threshold

    def collect(
        self,
        creator: Creator,
        filter: CollectFilter,
        browser: BrowserManager,
    ) -> list[CollectedVideo]:
        page = browser.new_page()
        logger.info("Navigating to creator homepage: %s", creator.homepage_url)
        page.goto(str(creator.homepage_url), wait_until="networkidle", timeout=30000)
        page.wait_for_selector('[class*="video-card"], [class*="ECMy_MlT"]', timeout=15000)

        results: list[CollectedVideo] = []
        seen_vids: set[str] = set()
        consecutive_out_of_window = 0

        while True:
            cards = parse_visible_cards(page)
            if not cards:
                logger.debug("No visible cards found, stopping scroll.")
                break

            any_in_window = False
            for card in cards:
                if card.vid in seen_vids:
                    continue
                seen_vids.add(card.vid)

                if card.published_at >= filter.end:
                    continue

                if card.published_at < filter.start:
                    consecutive_out_of_window += 1
                    continue

                consecutive_out_of_window = 0
                any_in_window = True
                results.append(card_to_collected(card))
                logger.debug(
                    "Collected video %s (published: %s)",
                    card.vid,
                    card.published_at.isoformat(),
                )

                if filter.max_videos and len(results) >= filter.max_videos:
                    logger.info("Reached max_videos limit (%d), stopping.", filter.max_videos)
                    page.close()
                    return results

            if not any_in_window and consecutive_out_of_window >= self._out_of_window_threshold:
                logger.info(
                    "Stopping: %d consecutive pages with no in-window videos.",
                    consecutive_out_of_window,
                )
                break

            _scroll_down(page)
            delay = random.uniform(*self._scroll_delay)
            logger.debug("Scrolled down, sleeping %.1f s...", delay)
            time.sleep(delay)

        page.close()
        logger.info("Collection complete: %d videos collected", len(results))
        return results


def _scroll_down(page) -> None:
    page.evaluate("window.scrollBy(0, window.innerHeight)")
    page.wait_for_timeout(500)
