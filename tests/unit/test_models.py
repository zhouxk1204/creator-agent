from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo, Video, VideoAsset, VideoStatus


def test_creator_minimal():
    c = Creator(
        id="douyin_12345",
        platform="douyin",
        platform_uid="12345",
        nickname="Test Creator",
        homepage_url="https://www.douyin.com/user/12345",
        added_at=datetime.now(UTC),
    )
    assert c.id == "douyin_12345"
    assert c.sync_status == "idle"


def test_creator_serialize():
    c = Creator(
        id="douyin_12345",
        platform="douyin",
        platform_uid="12345",
        nickname="Test",
        homepage_url="https://www.douyin.com/user/12345",
        added_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    d = c.model_dump(mode="json")
    assert d["id"] == "douyin_12345"
    assert d["sync_status"] == "idle"


def test_collected_video_defaults():
    cv = CollectedVideo(
        platform="douyin",
        platform_vid="abc123",
        title="Test Video",
        published_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    assert cv.stats.likes == 0
    assert cv.stats.comments == 0
    assert cv.description == ""
    assert cv.hashtags == []


def test_video_status_enum():
    assert VideoStatus.NEW.value == "NEW"
    assert VideoStatus.METADATA_SAVED.value == "METADATA_SAVED"
    assert VideoStatus.VIDEO_DOWNLOADED.value == "VIDEO_DOWNLOADED"


def test_video_defaults():
    v = Video(
        id="douyin_abc123",
        creator_id="douyin_12345",
        platform="douyin",
        platform_vid="abc123",
        title="Test",
        published_at=datetime(2024, 6, 1, tzinfo=UTC),
        collected_at=datetime(2024, 6, 2, tzinfo=UTC),
    )
    assert v.status == VideoStatus.NEW
    assert v.stats.likes == 0
    assert v.tags == []


def test_video_asset():
    v = Video(
        id="douyin_abc123",
        creator_id="douyin_12345",
        platform="douyin",
        platform_vid="abc123",
        title="Test",
        published_at=datetime(2024, 6, 1, tzinfo=UTC),
        collected_at=datetime(2024, 6, 2, tzinfo=UTC),
    )
    asset = VideoAsset(
        video=v,
        video_path=Path("/some/path/video.mp4"),
        cover_path=Path("/some/path/cover.jpg"),
    )
    assert asset.video_path is not None
    assert str(asset.video_path).endswith("video.mp4")
