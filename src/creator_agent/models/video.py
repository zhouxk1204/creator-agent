from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, HttpUrl


class VideoStats(BaseModel):
    likes: int = 0
    comments: int = 0
    favorites: int = 0
    shares: int = 0
    views: int | None = None


class CollectedVideo(BaseModel):
    platform: str
    platform_vid: str
    title: str
    description: str = ""
    video_url: HttpUrl | None = None
    cover_url: HttpUrl | None = None
    published_at: datetime
    stats: VideoStats = VideoStats()
    hashtags: list[str] = []


class VideoStatus(StrEnum):
    NEW = "NEW"
    METADATA_SAVED = "METADATA_SAVED"
    VIDEO_DOWNLOADED = "VIDEO_DOWNLOADED"


class Video(BaseModel):
    id: str
    creator_id: str
    platform: str
    platform_vid: str
    title: str
    description: str = ""
    cover_url: HttpUrl | None = None
    video_url: HttpUrl | None = None
    published_at: datetime
    duration_sec: int | None = None
    stats: VideoStats = VideoStats()
    tags: list[str] = []
    collected_at: datetime
    status: VideoStatus = VideoStatus.NEW
    # Per-video storage folder name (e.g. ``2026-07-11_张三``), relative to the
    # creator's ``videos/`` dir. Assigned by the repository at first upsert so
    # every save (metadata/video/cover) lands in the same folder, and so the
    # folder is stable across re-syncs. Empty for rows created before this field.
    storage_path: str = ""


class VideoAsset(BaseModel):
    video: Video
    video_path: Path | None = None
    cover_path: Path | None = None
    metadata_path: Path | None = None
