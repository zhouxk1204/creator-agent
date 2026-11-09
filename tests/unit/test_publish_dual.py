from types import SimpleNamespace
from unittest.mock import patch

from scripts import publish_dual


def make_publish_dir(root):
    folder = root / "job"
    folder.mkdir()
    (folder / "video.mp4").write_bytes(b"video")
    (folder / "title.txt").write_text("测试标题\n", encoding="utf-8")
    return folder


def make_job(folder):
    args = SimpleNamespace(
        schedule=None,
        tid=None,
        douyin_account="default",
        bilibili_account="default",
        only="douyin",
    )
    return publish_dual.PublishJob(folder, args)


def add_cover(folder, stem):
    path = folder / f"{stem}.jpg"
    path.write_bytes(b"image")
    return path


def assert_flag(command, flag, expected):
    assert command[command.index(flag) + 1] == str(expected)


def test_shared_douyin_covers_are_used_and_forwarded(tmp_path):
    folder = make_publish_dir(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    portrait = add_cover(shared, "cover-douyin-portrait")
    landscape = add_cover(shared, "cover-douyin-landscape")

    with patch.object(publish_dual, "SHARED_COVER_ROOT", shared):
        job = make_job(folder)
        commands = job.build_commands(headed=False)
        assert publish_dual.cover_label(job.cover_douyin_portrait) == f"{portrait.name}（公共）"

    assert job.cover_douyin_portrait == portrait
    assert job.cover_douyin_landscape == landscape
    command = commands[0][1]
    assert_flag(command, "--thumbnail-portrait", portrait)
    assert_flag(command, "--thumbnail-landscape", landscape)


def test_per_video_douyin_covers_override_shared_defaults(tmp_path):
    folder = make_publish_dir(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    add_cover(shared, "cover-douyin-portrait")
    add_cover(shared, "cover-douyin-landscape")
    portrait = add_cover(folder, "cover-douyin-portrait")
    landscape = add_cover(folder, "cover-douyin-landscape")

    with patch.object(publish_dual, "SHARED_COVER_ROOT", shared):
        job = make_job(folder)

    assert job.cover_douyin_portrait == portrait
    assert job.cover_douyin_landscape == landscape
    assert publish_dual.cover_label(portrait) == f"{portrait.name}（本次）"


def test_legacy_douyin_cover_remains_portrait_fallback(tmp_path):
    folder = make_publish_dir(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    legacy = add_cover(folder, "cover-douyin")
    landscape = add_cover(shared, "cover-douyin-landscape")

    with patch.object(publish_dual, "SHARED_COVER_ROOT", shared):
        job = make_job(folder)

    assert job.cover_douyin_portrait == legacy
    assert job.cover_douyin_landscape == landscape
