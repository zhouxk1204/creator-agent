from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from playwright.sync_api import Page

from creator_agent.collector.douyin.time_parser import parse_douyin_time
from creator_agent.models.video import CollectedVideo, VideoStats

logger = logging.getLogger(__name__)
_RE_VID = re.compile(r"(?:/video/|/share/video/)(\d+)")


@dataclass
class ParsedCard:
    vid: str
    title: str
    cover_url: str | None
    video_url: str | None
    published_at: datetime
    likes: int
    comments: int
    views: int | None


def parse_visible_cards(page: Page) -> list[ParsedCard]:
    js_code = """
    () => {
        const cards = document.querySelectorAll('[class*="video-card"], [class*="ECMy_MlT"]');
        return Array.from(cards).map(card => {
            const link = card.querySelector("a");
            const vidMatch = link ? link.href.match(/\\/video\\/(\\d+)/) : null;
            return {
                vid: vidMatch ? vidMatch[1] : (card.getAttribute("data-vid") || ""),
                title: (card.querySelector('[class*="title"]')?.textContent || "").trim(),
                coverUrl: card.querySelector("img")?.src || "",
                videoUrl: "",
                publishTime: (card.querySelector('[class*="publish-time"]')?.textContent || "").trim(),
                likes: parseInt(card.querySelector('[class*="digg-count"]')?.textContent || "0", 10),
                comments: parseInt(card.querySelector('[class*="comment-count"]')?.textContent || "0", 10),
                views: (() => {
                    const v = card.querySelector('[class*="play-count"]')?.textContent;
                    return v ? parseInt(v, 10) : null;
                })(),
                linkHref: link ? link.href : "",
            };
        });
    }
    """

    raw_cards: list[dict] = page.evaluate(js_code)
    results: list[ParsedCard] = []
    now = datetime.now()

    for raw in raw_cards:
        vid = raw.get("vid", "") or _extract_vid(raw.get("linkHref", ""))
        if not vid:
            logger.warning("Skipping card without vid")
            continue
        published = parse_douyin_time(raw.get("publishTime", "\u521a\u521a"), now)
        results.append(
            ParsedCard(
                vid=vid,
                title=raw.get("title", ""),
                cover_url=raw.get("coverUrl") or None,
                video_url=raw.get("videoUrl") or raw.get("linkHref") or None,
                published_at=published,
                likes=raw.get("likes", 0),
                comments=raw.get("comments", 0),
                views=raw.get("views"),
            )
        )
    return results


def _extract_vid(href: str) -> str:
    if m := _RE_VID.search(href):
        return m.group(1)
    return ""


def card_to_collected(card: ParsedCard) -> CollectedVideo:
    return CollectedVideo(
        platform="douyin",
        platform_vid=card.vid,
        title=card.title,
        video_url=card.video_url,
        cover_url=card.cover_url,
        published_at=card.published_at,
        stats=VideoStats(likes=card.likes, comments=card.comments, views=card.views),
    )
