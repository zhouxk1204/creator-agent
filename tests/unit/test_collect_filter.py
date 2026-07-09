from __future__ import annotations

from datetime import UTC, datetime, timedelta

from creator_agent.collector.base import (
    last_n_days_filter,
    latest_n_filter,
    yesterday_filter,
)


def test_yesterday_filter():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    f = yesterday_filter(now)
    assert f.end <= f.start + timedelta(days=1)
    assert f.start < f.end


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
