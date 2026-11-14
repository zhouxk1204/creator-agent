"""Rules for the Douyin covers: naming, gating and metadata write-back (both orientations)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from creator_agent.doraemon.covers import (
    cover_field,
    cover_filename,
    cover_plan,
    cover_title,
    existing_cover,
    landscape_filename,
    portrait_filename,
    set_landscape_cover,
    set_portrait_cover,
    stories_to_render,
)
from creator_agent.doraemon.metadata import (
    MetadataError,
    load_metadata,
    normalize_metadata,
    source_hash,
    update_metadata,
)


def make_metadata(tmp_path, *, zh_title: str | None = "机器人服") -> Path:
    episode = "0942"
    story = {
        "story_id": "942_1",
        "story_index": 1,
        "title": "ロボット服",
        "synopsis": "あらすじ",
        "image_file": "942_1.jpg",
    }
    data = normalize_metadata(
        {
            "schema_version": 2,
            "episode": episode,
            "episode_title": "ロボット服みらいラジオ",
            "broadcast_date": "2026-11-14",
            "url": "https://www.tv-asahi.co.jp/doraemon/story/0942/",
            "stories": [story],
        }
    )
    if zh_title is not None:
        digest = source_hash(data["stories"][0])
        data["stories"][0]["zh"] = {
            "title": zh_title,
            "synopsis": "中文简介",
            "source_hash": digest,
            "translated_at": "2026-11-14T05:00:00+00:00",
        }
        data["stories"][0]["publish"]["title"] = f"哆啦a梦942 {zh_title}"
    (tmp_path / "942_1.jpg").write_bytes(b"jpg")
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_filenames_follow_story_id():
    assert portrait_filename("942_1") == "942_1_douyin_portrait.jpg"
    assert landscape_filename("942_1") == "942_1_douyin_landscape.jpg"
    assert cover_filename("942_1") == portrait_filename("942_1")
    assert cover_filename("942_1", "landscape") == landscape_filename("942_1")
    assert cover_field("portrait") == "cover_portrait_path"
    assert cover_field("landscape") == "cover_landscape_path"


def test_cover_title_requires_current_translation(tmp_path):
    path = make_metadata(tmp_path, zh_title=None)
    data = load_metadata(path)
    with pytest.raises(MetadataError, match="中文翻译缺失"):
        cover_title(data["stories"][0])


def test_cover_title_returns_chinese_title(tmp_path):
    data = load_metadata(make_metadata(tmp_path))
    assert cover_title(data["stories"][0]) == "机器人服"


def test_set_portrait_cover_writes_relative_name(tmp_path):
    path = make_metadata(tmp_path)
    cover = tmp_path / portrait_filename("942_1")
    cover.write_bytes(b"jpg")
    data = set_portrait_cover(path, "942_1", cover)
    assert data["stories"][0]["publish"]["douyin"]["cover_portrait_path"] == "942_1_douyin_portrait.jpg"
    assert load_metadata(path)["stories"][0]["publish"]["douyin"]["cover_portrait_path"] == portrait_filename("942_1")


def test_set_portrait_cover_keeps_handmade_cover_without_force(tmp_path):
    path = make_metadata(tmp_path)
    handmade = tmp_path / "custom.jpg"
    handmade.write_bytes(b"jpg")
    set_portrait_cover(path, "942_1", handmade)
    generated = tmp_path / portrait_filename("942_1")
    generated.write_bytes(b"jpg")
    with pytest.raises(MetadataError, match="已有自定义"):
        set_portrait_cover(path, "942_1", generated)
    assert load_metadata(path)["stories"][0]["publish"]["douyin"]["cover_portrait_path"] == "custom.jpg"


def test_set_portrait_cover_force_replaces(tmp_path):
    path = make_metadata(tmp_path)
    handmade = tmp_path / "custom.jpg"
    handmade.write_bytes(b"jpg")
    set_portrait_cover(path, "942_1", handmade)
    generated = tmp_path / portrait_filename("942_1")
    generated.write_bytes(b"jpg")
    data = set_portrait_cover(path, "942_1", generated, overwrite=True)
    assert data["stories"][0]["publish"]["douyin"]["cover_portrait_path"] == portrait_filename("942_1")


def test_cover_plan_skips_when_cover_exists(tmp_path):
    path = make_metadata(tmp_path)
    cover = tmp_path / portrait_filename("942_1")
    cover.write_bytes(b"jpg")
    update_metadata(
        path,
        lambda data: data["stories"][0]["publish"]["douyin"].update(
            {"cover_portrait_path": portrait_filename("942_1")}
        ),
    )
    data = load_metadata(path)
    assert existing_cover(path, data["stories"][0]) == cover
    assert cover_plan(path, data["stories"][0], force=False) is None
    target, title = cover_plan(path, data["stories"][0], force=True)
    assert target == cover
    assert title == "机器人服"


def test_stories_to_render_selects_requested(tmp_path):
    data = load_metadata(make_metadata(tmp_path))
    assert [story["story_id"] for story in stories_to_render(data, None)] == ["942_1"]
    assert [story["story_id"] for story in stories_to_render(data, ["942_1"])] == ["942_1"]
    with pytest.raises(MetadataError):
        stories_to_render(data, ["999_1"])


def test_set_landscape_cover_writes_its_own_field(tmp_path):
    path = make_metadata(tmp_path)
    portrait = tmp_path / portrait_filename("942_1")
    portrait.write_bytes(b"jpg")
    landscape = tmp_path / landscape_filename("942_1")
    landscape.write_bytes(b"jpg")
    set_portrait_cover(path, "942_1", portrait)
    data = set_landscape_cover(path, "942_1", landscape)
    douyin = data["stories"][0]["publish"]["douyin"]
    assert douyin["cover_portrait_path"] == portrait_filename("942_1")
    assert douyin["cover_landscape_path"] == landscape_filename("942_1")


def test_cover_plan_is_per_orientation(tmp_path):
    path = make_metadata(tmp_path)
    portrait = tmp_path / portrait_filename("942_1")
    portrait.write_bytes(b"jpg")
    set_portrait_cover(path, "942_1", portrait)
    data = load_metadata(path)
    story = data["stories"][0]
    # Portrait already exists, landscape still missing.
    assert cover_plan(path, story, force=False, orientation="portrait") is None
    plan = cover_plan(path, story, force=False, orientation="landscape")
    assert plan is not None
    assert plan[0] == tmp_path / landscape_filename("942_1")
    assert plan[1] == "机器人服"
