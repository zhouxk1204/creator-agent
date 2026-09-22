from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from creator_agent.collector.base import (
    day_filter,
    last_n_days_filter,
    latest_n_filter,
    today_filter,
    yesterday_filter,
)


def test_yesterday_filter():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    f = yesterday_filter(now)
    assert f.end <= f.start + timedelta(days=1)
    assert f.start < f.end


def test_today_filter():
    # 2024-06-15 12:00 UTC == 2024-06-15 20:00 CST, so "today" is Jun 15 CST.
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    f = today_filter(now)
    delta = f.end - f.start
    assert delta == timedelta(days=1)
    assert f.start < f.end
    # now must fall inside [start, end).
    assert f.start <= now < f.end


def test_today_filter_spans_cn_calendar_day():
    # 2024-06-15 16:00 UTC == 2024-06-16 00:00 CST -> "today" in CN is Jun 16.
    now = datetime(2024, 6, 15, 16, 0, 1, tzinfo=UTC)
    f = today_filter(now)
    # Window start = 2024-06-16 00:00 CST == 2024-06-15 16:00 UTC.
    assert f.start == datetime(2024, 6, 15, 16, 0, 0, tzinfo=UTC)
    assert f.end == datetime(2024, 6, 16, 16, 0, 0, tzinfo=UTC)


def test_day_filter_covers_whole_cn_day():
    f = day_filter(date(2024, 6, 15))
    # 2024-06-15 00:00 CST == 2024-06-14 16:00 UTC.
    assert f.start == datetime(2024, 6, 14, 16, 0, 0, tzinfo=UTC)
    assert f.end == datetime(2024, 6, 15, 16, 0, 0, tzinfo=UTC)
    assert f.end - f.start == timedelta(days=1)
    assert f.max_videos is None


def test_day_filter_matches_today_for_same_day():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)  # 2024-06-15 20:00 CST
    assert day_filter(date(2024, 6, 15)) == today_filter(now)


def test_last_n_days_filter():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    f = last_n_days_filter(3, now)
    delta = f.end - f.start
    assert delta.days == 3
    assert f.start < f.end


def test_latest_n_filter():
    f = latest_n_filter(10)
    assert f.max_videos == 10
    assert f.start < f.end


def test_filter_has_no_max_by_default():
    f = yesterday_filter()
    assert f.max_videos is None
