from __future__ import annotations

from datetime import UTC, datetime

import pytest

from creator_agent.models.creator import Creator
from creator_agent.models.video import Video, VideoStatus
from creator_agent.storage.file_storage import FileStorage


@pytest.fixture
def storage(tmp_path):
    return FileStorage(tmp_path / "storage")


@pytest.fixture
def sample_creator():
    return Creator(
        id="douyin_test123",
        platform="douyin",
        platform_uid="test123",
        nickname="Test Creator",
        homepage_url="https://www.douyin.com/user/test123",
        added_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


@pytest.fixture
def sample_video():
    return Video(
        id="douyin_abc123",
        creator_id="douyin_test123",
        platform="douyin",
        platform_vid="abc123",
        title="Test Video",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
        collected_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        status=VideoStatus.NEW,
        storage_path="2024-06-15_Test_Creator",
    )


def test_save_and_load_creator_profile(storage, sample_creator):
    storage.save_creator_profile(sample_creator)
    loaded = storage.load_creator_profile(sample_creator)
    assert loaded is not None
    assert loaded.nickname == "Test Creator"
    assert loaded.id == "douyin_test123"


def test_creator_folder_uses_sanitized_nickname(storage, sample_creator):
    path = storage.save_creator_profile(sample_creator)
    # Folder is the sanitized nickname ("Test Creator" -> "Test_Creator"), not the id.
    assert path.parent.name == "Test_Creator"


def test_save_avatar(storage, sample_creator):
    path = storage.save_avatar(sample_creator, b"fake_avatar_data")
    assert path.exists()
    assert path.read_bytes() == b"fake_avatar_data"


def test_save_and_load_metadata(storage, sample_creator, sample_video):
    storage.save_metadata(sample_creator, sample_video)
    loaded = storage.load_metadata(sample_creator, sample_video)
    assert loaded is not None
    assert loaded.title == "Test Video"
    assert loaded.status == VideoStatus.NEW


def test_save_video_and_cover_land_in_storage_path_folder(storage, sample_creator, sample_video):
    video_path = storage.save_video_file(sample_creator, sample_video, b"fake_video_data")
    cover_path = storage.save_cover(sample_creator, sample_video, b"fake_cover_data")
    assert video_path.exists()
    assert cover_path.exists()
    assert video_path.read_bytes() == b"fake_video_data"
    assert cover_path.read_bytes() == b"fake_cover_data"
    # Both assets share the storage_path folder, alongside metadata.
    assert video_path.parent == cover_path.parent
    assert video_path.parent.name == "2024-06-15_Test_Creator"


def test_list_videos(storage, sample_creator, sample_video):
    storage.save_metadata(sample_creator, sample_video)
    videos = storage.list_videos(sample_creator)
    assert "2024-06-15_Test_Creator" in videos


def test_video_exists(storage, sample_creator, sample_video):
    assert not storage.video_exists(sample_creator, sample_video)
    storage.save_metadata(sample_creator, sample_video)
    assert storage.video_exists(sample_creator, sample_video)


def test_video_dir_falls_back_to_id_without_storage_path(storage, sample_creator):
    # Rows created before storage_path existed fall back to video.id as the folder.
    legacy = Video(
        id="douyin_legacy1",
        creator_id="douyin_test123",
        platform="douyin",
        platform_vid="legacy1",
        title="Legacy",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
        collected_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        status=VideoStatus.NEW,
        storage_path="",
    )
    path = storage.save_video_file(sample_creator, legacy, b"x")
    assert path.parent.name == "douyin_legacy1"
