from __future__ import annotations

import logging
import random
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from creator_agent.collector.base import CollectFilter, Collector
from creator_agent.collector.douyin.meta import fetch_video_meta
from creator_agent.collector.douyin.parser import collect_video_links, meta_to_collected
from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo

if TYPE_CHECKING:
    from creator_agent.browser.manager import BrowserManager

logger = logging.getLogger(__name__)

OUT_OF_WINDOW_PAGE_THRESHOLD = 2
# A too-old video much older than the window start (likely a pinned/featured
# video at the top of the page) is "stale" and does NOT count toward the
# out-of-window stop threshold - otherwise pinned videos trigger an early stop
# before the chronological feed is reached. Only "recently past-window" videos
# (within this margin of the start) count, i.e. the feed scrolling past the
# window boundary. Pinned videos are typically months/years old; the feed just
# past the window is within days, so a 30-day margin separates them cleanly.
STALE_MARGIN_DAYS = 30
# Per-video metadata navigation timeout. The aweme-detail XHR + CDN URL are
# captured in this one navigation.
_META_TIMEOUT_SEC = 90


class DouyinCollector(Collector):
    platform: str = "douyin"

    def __init__(
        self,
        scroll_delay: tuple[float, float] = (1.0, 3.0),
        out_of_window_threshold: int = OUT_OF_WINDOW_PAGE_THRESHOLD,
        meta_timeout_sec: int = _META_TIMEOUT_SEC,
        stale_margin_days: int = STALE_MARGIN_DAYS,
    ) -> None:
        self._scroll_delay = scroll_delay
        self._out_of_window_threshold = out_of_window_threshold
        self._meta_timeout_sec = meta_timeout_sec
        self._stale_margin = timedelta(days=stale_margin_days)

    def collect(
        self,
        creator: Creator,
        filter: CollectFilter,
        browser: BrowserManager,
    ) -> list[CollectedVideo]:
        page = browser.new_page()
        logger.info("Navigating to creator homepage: %s", creator.homepage_url)
        page.goto(str(creator.homepage_url), wait_until="load", timeout=30000)
        page.wait_for_selector('a[href*="/video/"]', timeout=15000)

        results: list[CollectedVideo] = []
        seen_vids: set[str] = set()
        consecutive_out_of_window = 0

        try:
            while True:
                links = collect_video_links(page)
                new_links = [ln for ln in links if ln["vid"] not in seen_vids]
                if not new_links:
                    logger.debug("No new video links after scroll, stopping.")
                    break

                any_in_window = False
                stop = False
                for link in new_links:
                    seen_vids.add(link["vid"])
                    # One navigation per video: gives published_at (to classify
                    # against the window) AND the rich metadata we keep for
                    # in-window videos. The grid card has no publish time, so
                    # this navigation is unavoidable for time-window filtering.
                    try:
                        meta = fetch_video_meta(link["href"], browser, self._meta_timeout_sec)
                    except Exception as e:
                        logger.warning("Metadata fetch failed for %s, skipping: %s", link["href"], e)
                        continue
                    published = meta.published_at

                    # Future video (newer than the window end) - keep scrolling.
                    if published is not None and published >= filter.end:
                        continue
                    # Too old. Distinguish "stale" (far older than the window
                    # start - likely a pinned/featured video) from "recently
                    # past-window" (the chronological feed scrolling past the
                    # window). Stale videos don't count toward the stop
                    # threshold, so a block of pinned videos at the top doesn't
                    # abort collection before the real feed is reached.
                    if published is not None and published < filter.start:
                        if published < filter.start - self._stale_margin:
                            logger.debug(
                                "Skipping stale (likely pinned) video %s (published %s).",
                                link["vid"],
                                published.isoformat(),
                            )
                            continue
                        consecutive_out_of_window += 1
                        if consecutive_out_of_window >= self._out_of_window_threshold:
                            logger.info(
                                "Stopping: %d consecutive out-of-window videos.",
                                consecutive_out_of_window,
                            )
                            stop = True
                            break
                        continue

                    # In window (or published_at unknown -> collected, not counted
                    # as out-of-window; meta_to_collected warns + falls back to now).
                    consecutive_out_of_window = 0
                    any_in_window = True
                    cv = meta_to_collected(meta, link["vid"], link["href"])
                    results.append(cv)
                    logger.info(
                        "Collected video %s (published: %s)",
                        cv.platform_vid,
                        cv.published_at.isoformat(),
                    )

                    if filter.max_videos and len(results) >= filter.max_videos:
                        logger.info("Reached max_videos limit (%d), stopping.", filter.max_videos)
                        return results

                if stop:
                    break
                if not any_in_window and consecutive_out_of_window >= self._out_of_window_threshold:
                    break

                _scroll_down(page)
                delay = random.uniform(*self._scroll_delay)
                logger.debug("Scrolled down, sleeping %.1f s...", delay)
                time.sleep(delay)
        finally:
            page.close()

        logger.info("Collection complete: %d videos collected", len(results))
        return results


def _scroll_down(page) -> None:
    page.evaluate("window.scrollBy(0, window.innerHeight)")
    page.wait_for_timeout(500)
