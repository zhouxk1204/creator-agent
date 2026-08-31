from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel

from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo

if TYPE_CHECKING:
    from creator_agent.browser.manager import BrowserManager


class CollectFilter(BaseModel):
    start: datetime
    end: datetime
    max_videos: int | None = None


def yesterday_filter(now: datetime | None = None) -> CollectFilter:
    now = now or datetime.now(timezone.utc)
    tz_cn = timezone(timedelta(hours=8))
    now_cn = now.astimezone(tz_cn)
    start_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
    return CollectFilter(
        start=start_cn.astimezone(timezone.utc),
        end=end_cn.astimezone(timezone.utc),
    )


def today_filter(now: datetime | None = None) -> CollectFilter:
    """Window covering the current calendar day in CN time (today 00:00 -> tomorrow 00:00)."""
    now = now or datetime.now(timezone.utc)
    tz_cn = timezone(timedelta(hours=8))
    now_cn = now.astimezone(tz_cn)
    start_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
    end_cn = start_cn + timedelta(days=1)
    return CollectFilter(
        start=start_cn.astimezone(timezone.utc),
        end=end_cn.astimezone(timezone.utc),
    )


def day_filter(day: date) -> CollectFilter:
    """Window covering a single calendar day in CN time (day 00:00 -> next day 00:00).

    Use this to collect a specific day's videos, e.g. ``day_filter(date(2026, 7, 11))``.
    """
    tz_cn = timezone(timedelta(hours=8))
    start_cn = datetime(day.year, day.month, day.day, tzinfo=tz_cn)
    end_cn = start_cn + timedelta(days=1)
    return CollectFilter(
        start=start_cn.astimezone(timezone.utc),
        end=end_cn.astimezone(timezone.utc),
    )


def last_n_days_filter(n: int, now: datetime | None = None) -> CollectFilter:
    now = now or datetime.now(timezone.utc)
    tz_cn = timezone(timedelta(hours=8))
    now_cn = now.astimezone(tz_cn)
    # End = end of current day in CN (tomorrow midnight). Start = N full days back.
    # This ensures videos published anywhere in today's CN calendar day are in-window.
    end_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start_cn = end_cn - timedelta(days=n)
    return CollectFilter(
        start=start_cn.astimezone(timezone.utc),
        end=end_cn.astimezone(timezone.utc),
    )


def latest_n_filter(n: int) -> CollectFilter:
    return CollectFilter(
        start=datetime(2000, 1, 1, tzinfo=timezone.utc),
        end=datetime.now(timezone.utc) + timedelta(days=1),
        max_videos=n,
    )


class Collector(ABC):
    platform: str = ""

    @abstractmethod
    def collect(
        self,
        creator: Creator,
        filter: CollectFilter,
        browser: BrowserManager,
    ) -> list[CollectedVideo]: ...
