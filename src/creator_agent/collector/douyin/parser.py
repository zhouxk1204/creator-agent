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
    ``[{"vid": str, "href": str, "title": str, "cover": str}, ...]``. ``href`` is
    the canonical ``https://www.douyin.com/video/{vid}`` URL - the scraped
    ``a.href`` often carries tracking/spider query params (e.g.
    ``?source=Baiduspider``) that make Douyin serve a variant page which does
    not fire the aweme/detail XHR, so metadata capture fails. The grid card
    carries no publish time, so ``published_at`` and rich metadata are resolved
    per-video via :func:`fetch_video_meta`.

    ``cover`` is the card's ``<img>`` thumbnail src - this is the cover users
    actually see on Douyin and is preferred over the aweme-detail ``video.cover``
    field (which can resolve to a different, processed image variant).
    """
    js_code = r"""() => {
        const seen = new Set();
        const out = [];
        const pickCover = (a) => {
            const imgs = a.querySelectorAll('img');
            // Prefer a douyinpic CDN image (the real cover); the like icon is
            // an inline <svg>, not an <img>, so the card's only <img> is the cover.
            for (const img of imgs) {
                const src = img.src || img.getAttribute('src') || img.getAttribute('data-src') || '';
                if (src && (src.indexOf('douyinpic') !== -1 || src.indexOf('tplv-dy') !== -1)) return src;
            }
            for (const img of imgs) {
                const src = img.src || img.getAttribute('src') || img.getAttribute('data-src') || '';
                if (src) return src;
            }
            return '';
        };
        for (const a of document.querySelectorAll('a[href*="/video/"]')) {
            const m = a.href.match(/\/video\/(\d+)/);
            if (!m || seen.has(m[1])) continue;
            seen.add(m[1]);
            out.push({
                vid: m[1],
                href: 'https://www.douyin.com/video/' + m[1],
                title: (a.textContent || '').trim().slice(0, 200),
                cover: pickCover(a),
            });
        }
        return out;
    }
    """
    return list(page.evaluate(js_code))


def extract_vid(href: str) -> str:
    if m := _RE_VID.search(href):
        return m.group(1)
    return ""


def meta_to_collected(meta: VideoMeta, vid: str, page_url: str, cover: str | None = None) -> CollectedVideo:
    """Map a resolved :class:`VideoMeta` to a :class:`CollectedVideo`.

    ``published_at`` falls back to ``now`` when the aweme detail was not
    captured - the video is still collectable, but it cannot be time-window
    filtered, so callers should treat ``None``-detail videos as in-window and
    log a warning.

    ``cover`` is the list-page card thumbnail (the cover users see on Douyin);
    it takes priority over ``meta.cover_url`` (the aweme-detail
    ``video.cover`` field, which can resolve to a different processed image).
    """
    if meta.published_at is None:
        logger.warning("Video %s has no published_at (detail XHR not captured); using now.", vid)
    return CollectedVideo(
        platform="douyin",
        platform_vid=vid,
        title=meta.title or "",
        description=meta.description,
        video_url=str(meta.cdn_url) if meta.cdn_url else page_url,
        cover_url=cover or meta.cover_url,
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
