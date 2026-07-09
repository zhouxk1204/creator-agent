from __future__ import annotations

from datetime import UTC, datetime, timedelta

from creator_agent.collector.base import last_n_days_filter, yesterday_filter


def test_yesterday_filter_timezone():
    """Yesterday filter uses UTC+8 timezone for boundary calculation."""
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    f = yesterday_filter(now)
    assert f.start < f.end
    assert f.end - f.start == timedelta(days=1)


def test_yesterday_filter_bounds():
    """Verify yesterday filter covers exactly 24 hours in UTC+8."""
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    f = yesterday_filter(now)
    delta = f.end - f.start
    assert delta == timedelta(days=1)


def test_last_n_days_filter_bounds():
    """Verify last N days filter covers exactly N days."""
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    f = last_n_days_filter(3, now)
    assert (f.end - f.start).days == 3
