from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, HttpUrl


class Creator(BaseModel):
    """Creator domain model."""

    id: str
    platform: Literal["douyin", "bilibili", "youtube"]
    platform_uid: str
    nickname: str
    homepage_url: HttpUrl
    avatar_url: HttpUrl | None = None
    bio: str | None = None
    added_at: datetime
    last_synced_at: datetime | None = None
    sync_status: Literal["idle", "syncing", "failed", "ok", "partial"] = "idle"
    last_error: str | None = None
