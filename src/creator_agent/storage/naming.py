"""Folder-naming helpers shared by the repository and file storage.

The on-disk layout is ``storage/{creator_nickname}/videos/{video_folder}/``
where ``video_folder`` is ``{YYYY-MM-DD}_{nickname}`` with a ``_2``, ``_3`` ...
suffix when a creator posts multiple videos on the same (China-calendar) day.

Sanitization lives here (not in the callers) so the repository and file storage
always agree on what a given nickname/date maps to on disk.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Characters illegal in Windows folder names (also reserved on macOS/Linux).
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Douyin is a CN platform; the "day" of a video is its China-calendar day.
_CN_TZ = timezone(timedelta(hours=8))


def sanitize_name(name: str, fallback: str = "unknown") -> str:
    """Make a string safe to use as a single folder-name segment.

    Replaces illegal chars with ``_``, collapses whitespace, strips leading/
    trailing dots and underscores (Windows forbids trailing dots), and falls
    back when nothing usable remains.
    """
    cleaned = _ILLEGAL.sub("_", name)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned or fallback


def cn_date_str(published_at: datetime) -> str:
    """``YYYY-MM-DD`` for ``published_at`` in the China timezone."""
    return published_at.astimezone(_CN_TZ).strftime("%Y-%m-%d")


def video_folder_base(nickname: str, published_at: datetime) -> str:
    """The collision-free base folder name: ``{YYYY-MM-DD}_{nickname}``."""
    return f"{cn_date_str(published_at)}_{sanitize_name(nickname)}"


def video_folder_name(nickname: str, published_at: datetime, same_day_count: int) -> str:
    """Folder name for a video.

    ``same_day_count`` is how many videos are already stored for this creator on
    the same China-calendar day: 0 -> no suffix, 1 -> ``_2``, 2 -> ``_3``, ...
    """
    base = video_folder_base(nickname, published_at)
    suffix = "" if same_day_count == 0 else f"_{same_day_count + 1}"
    return f"{base}{suffix}"
