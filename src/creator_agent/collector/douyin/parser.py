from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from playwright.sync_api import Page

from creator_agent.collector.douyin.meta import VideoMeta
from creator_agent.models.video import CollectedVideo, VideoStats

logger = logging.getLogger(__name__)
_RE_VID = re.compile(r"/video/(\d+)")


def collect_video_links(page: Page) -> list[dict]:
    """Scrape visible ``/video/{vid}`` links from the creator homepage.

    Returns links in DOM order (newest first on Douyin), deduplicated by vid:
    ``[{"vid": str, "href": str, "title": str}, ...]``. The grid card carries
    no publish time, so only the link is collected here; ``published_at`` and
    rich metadata are resolved per-video via :func:`fetch_video_meta`.
    """
    js_code = r"""() => {
        const seen = new Set();
        const out = [];
        for (const a of document.querySelectorAll('a[href*="/video/"]')) {
            const m = a.href.match(/\/video\/(\d+)/);
            if (!m || seen.has(m[1])) continue;
            seen.add(m[1]);
            out.push({vid: m[1], href: a.href, title: (a.textContent || '').trim().slice(0, 200)});
        }
        return out;
    }
    """
    return list(page.evaluate(js_code))


def extract_vid(href: str) -> str:
    if m := _RE_VID.search(href):
        return m.group(1)
    return ""


def meta_to_collected(meta: VideoMeta, vid: str, page_url: str) -> CollectedVideo:
    """Map a resolved :class:`VideoMeta` to a :class:`CollectedVideo`.

    ``published_at`` falls back to ``now`` when the aweme detail was not
    captured - the video is still collectable, but it cannot be time-window
    filtered, so callers should treat ``None``-detail videos as in-window and
    log a warning.
    """
    if meta.published_at is None:
        logger.warning("Video %s has no published_at (detail XHR not captured); using now.", vid)
    return CollectedVideo(
        platform="douyin",
        platform_vid=vid,
        title=meta.title or "",
        description=meta.description,
        video_url=page_url,
        cover_url=meta.cover_url,
        published_at=meta.published_at or datetime.now(UTC),
        stats=VideoStats(
            likes=meta.likes,
            comments=meta.comments,
            favorites=meta.favorites,
            shares=meta.shares,
            views=meta.views,
        ),
        hashtags=list(meta.tags),
    )
