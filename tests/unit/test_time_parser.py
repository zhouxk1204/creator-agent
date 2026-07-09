from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from creator_agent.collector.douyin.time_parser import parse_douyin_time


def test_just_now():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = parse_douyin_time("\u521a\u521a", now)
    assert result == now


def test_minutes_ago():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = parse_douyin_time("5\u5206\u949f\u524d", now)
    assert result == now - timedelta(minutes=5)


def test_hours_ago():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = parse_douyin_time("3\u5c0f\u65f6\u524d", now)
    assert result == now - timedelta(hours=3)


def test_yesterday():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = parse_douyin_time("\u6628\u5929", now)
    assert result == now - timedelta(days=1)


def test_days_ago():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = parse_douyin_time("7\u5929\u524d", now)
    assert result == now - timedelta(days=7)


def test_weeks_ago():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = parse_douyin_time("2\u5468\u524d", now)
    assert result == now - timedelta(weeks=2)


def test_months_ago():
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = parse_douyin_time("3\u4e2a\u6708\u524d", now)
    assert result == now - timedelta(days=90)


def test_date_format():
    """Date strings are in UTC+8 by convention, convert to UTC."""
    tz_cn = timezone(timedelta(hours=8))
    result = parse_douyin_time("2024-06-01")
    expected = datetime(2024, 6, 1, tzinfo=tz_cn).astimezone(UTC)
    assert result == expected


def test_date_format_slash():
    tz_cn = timezone(timedelta(hours=8))
    result = parse_douyin_time("2024/06/01")
    expected = datetime(2024, 6, 1, tzinfo=tz_cn).astimezone(UTC)
    assert result == expected


def test_date_format_dot():
    tz_cn = timezone(timedelta(hours=8))
    result = parse_douyin_time("2024.06.01")
    expected = datetime(2024, 6, 1, tzinfo=tz_cn).astimezone(UTC)
    assert result == expected


def test_invalid_format():
    import pytest

    with pytest.raises(ValueError):
        parse_douyin_time("invalid time string")
