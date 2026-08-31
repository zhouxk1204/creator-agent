from __future__ import annotations

from datetime import UTC, datetime

import pytest

from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo, VideoStats, VideoStatus
from creator_agent.repository.sqlite_repo import Repository


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "test.db"
    r = Repository(db)
    yield r
    r.close()


@pytest.fixture
def sample_creator():
    return Creator(
        id="douyin_test123",
        platform="douyin",
        platform_uid="test123",
        nickname="Test Creator",
        homepage_url="https://www.douyin.com/user/test123",
        added_at=datetime(2024, 6, 1, tzinfo=UTC),
        sync_status="idle",
    )


def test_add_and_get_creator(repo, sample_creator):
    repo.add_creator(sample_creator)
    loaded = repo.get_creator("douyin_test123")
    assert loaded is not None
    assert loaded.nickname == "Test Creator"
    assert loaded.platform == "douyin"


def test_list_creators(repo):
    c1 = Creator(
        id="douyin_a",
        platform="douyin",
        platform_uid="a",
        nickname="A",
        homepage_url="https://douyin.com/user/a",
        added_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    c2 = Creator(
        id="douyin_b",
        platform="douyin",
        platform_uid="b",
        nickname="B",
        homepage_url="https://douyin.com/user/b",
        added_at=datetime(2024, 6, 2, tzinfo=UTC),
    )
    repo.add_creator(c1)
    repo.add_creator(c2)
    all_c = repo.list_creators()
    assert len(all_c) == 2


def test_upsert_collected_new(repo, sample_creator):
    repo.add_creator(sample_creator)
    cv = CollectedVideo(
        platform="douyin",
        platform_vid="abc123",
        title="Test Video",
        description="A test video",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
        stats=VideoStats(likes=100, comments=10),
    )
    video = repo.upsert_collected(sample_creator, cv)
    assert video.id == "douyin_abc123"
    assert video.status == VideoStatus.NEW
    assert video.stats.likes == 100
    assert video.storage_path == "2024-06-15"


def test_upsert_collected_same_day_collision_suffix(repo, sample_creator):
    repo.add_creator(sample_creator)
    cv1 = CollectedVideo(
        platform="douyin",
        platform_vid="vid1",
        title="First same-day",
        published_at=datetime(2024, 6, 15, 10, 0, tzinfo=UTC),
    )
    cv2 = CollectedVideo(
        platform="douyin",
        platform_vid="vid2",
        title="Second same-day",
        published_at=datetime(2024, 6, 15, 15, 0, tzinfo=UTC),
    )
    cv3 = CollectedVideo(
        platform="douyin",
        platform_vid="vid3",
        title="Different day",
        published_at=datetime(2024, 6, 16, tzinfo=UTC),
    )
    v1 = repo.upsert_collected(sample_creator, cv1)
    v2 = repo.upsert_collected(sample_creator, cv2)
    v3 = repo.upsert_collected(sample_creator, cv3)
    assert v1.storage_path == "2024-06-15"
    assert v2.storage_path == "2024-06-15_2"
    assert v3.storage_path == "2024-06-16"


def test_upsert_collected_duplicate_keeps_storage_path(repo, sample_creator):
    repo.add_creator(sample_creator)
    cv = CollectedVideo(
        platform="douyin",
        platform_vid="abc123",
        title="Original",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
    )
    v1 = repo.upsert_collected(sample_creator, cv)
    assert v1.storage_path == "2024-06-15"

    # Re-sync: same video collected again -> UPDATE, storage_path must be stable.
    cv2 = CollectedVideo(
        platform="douyin",
        platform_vid="abc123",
        title="Updated Title",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
    )
    v2 = repo.upsert_collected(sample_creator, cv2)
    assert v2.title == "Updated Title"
    assert v2.storage_path == "2024-06-15"


def test_advance_status(repo, sample_creator):
    repo.add_creator(sample_creator)
    cv = CollectedVideo(
        platform="douyin",
        platform_vid="abc123",
        title="Test",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
    )
    video = repo.upsert_collected(sample_creator, cv)
    assert video.status == VideoStatus.NEW

    repo.advance_status(video.id, VideoStatus.METADATA_SAVED)
    updated = repo.get_video(video.id)
    assert updated.status == VideoStatus.METADATA_SAVED


def test_list_videos_by_status(repo, sample_creator):
    repo.add_creator(sample_creator)
    cv1 = CollectedVideo(
        platform="douyin",
        platform_vid="v1",
        title="V1",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
    )
    cv2 = CollectedVideo(
        platform="douyin",
        platform_vid="v2",
        title="V2",
        published_at=datetime(2024, 6, 14, tzinfo=UTC),
    )
    v1 = repo.upsert_collected(sample_creator, cv1)
    v2 = repo.upsert_collected(sample_creator, cv2)
    repo.advance_status(v1.id, VideoStatus.METADATA_SAVED)

    new_videos = repo.list_videos_by_status([VideoStatus.NEW])
    assert len(new_videos) == 1
    assert new_videos[0].id == v2.id


def test_sync_history(repo, sample_creator):
    repo.add_creator(sample_creator)
    sync_id = repo.start_sync("douyin_test123")
    assert sync_id > 0
    repo.finish_sync(sync_id, "ok", 5, 3)


def test_remove_creator(repo, sample_creator):
    repo.add_creator(sample_creator)
    repo.remove_creator("douyin_test123")
    assert repo.get_creator("douyin_test123") is None
