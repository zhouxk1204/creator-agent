from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_TZ_CN = timezone(timedelta(hours=8))
_RE_JUST_NOW = re.compile(r"刚刚")
_RE_MINUTES = re.compile(r"(\d+)分钟前")
_RE_HOURS = re.compile(r"(\d+)小时前")
_RE_YESTERDAY = re.compile(r"昨天")
_RE_DAYS = re.compile(r"(\d+)天前")
_RE_WEEKS = re.compile(r"(\d+)周前")
_RE_MONTHS = re.compile(r"(\d+)个月前")
_RE_DATE = re.compile(r"^(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})$")


def parse_douyin_time(text: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    stripped = text.strip()

    if _RE_JUST_NOW.match(stripped):
        return now
    if m := _RE_MINUTES.match(stripped):
        return now - timedelta(minutes=int(m.group(1)))
    if m := _RE_HOURS.match(stripped):
        return now - timedelta(hours=int(m.group(1)))
    if _RE_YESTERDAY.match(stripped):
        return now - timedelta(days=1)
    if m := _RE_DAYS.match(stripped):
        return now - timedelta(days=int(m.group(1)))
    if m := _RE_WEEKS.match(stripped):
        return now - timedelta(weeks=int(m.group(1)))
    if m := _RE_MONTHS.match(stripped):
        return now - timedelta(days=int(m.group(1)) * 30)
    if m := _RE_DATE.match(stripped):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dt_cn = datetime(year, month, day, tzinfo=_TZ_CN)
        return dt_cn.astimezone(timezone.utc)

    raise ValueError(f"Cannot parse Douyin time string: {text!r}")
