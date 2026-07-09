from __future__ import annotations

from datetime import UTC, datetime

from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo, VideoStatus
from creator_agent.repository.sqlite_repo import Repository
from creator_agent.storage.file_storage import FileStorage


def test_video_status_transitions(tmp_path):
    """Test that upsert_collected preserves status and advance_status works."""
    db = tmp_path / "test.db"
    repo = Repository(db)
    _ = FileStorage(tmp_path / "storage")

    creator = Creator(
        id="douyin_999",
        platform="douyin",
        platform_uid="999",
        nickname="Test",
        homepage_url="https://douyin.com/user/999",
        added_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    repo.add_creator(creator)

    cv = CollectedVideo(
        platform="douyin",
        platform_vid="v999",
        title="Resume Test",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
    )

    video = repo.upsert_collected(creator, cv)
    assert video.status == VideoStatus.NEW

    repo.advance_status(video.id, VideoStatus.METADATA_SAVED)
    updated = repo.get_video(video.id)
    assert updated.status == VideoStatus.METADATA_SAVED

    cv2 = CollectedVideo(
        platform="douyin",
        platform_vid="v999",
        title="Resume Test (updated)",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
    )

    reupserted = repo.upsert_collected(creator, cv2)
    assert reupserted.status == VideoStatus.METADATA_SAVED
    assert reupserted.title == "Resume Test (updated)"
