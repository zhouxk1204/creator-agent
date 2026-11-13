from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from creator_agent.doraemon import metadata as meta


def sources():
    return {
        "episode": "941",
        "broadcast_date": "2026年11月7日放送",
        "episode_title": "「ドラえもん」",
        "stories": [
            {"title": f"「物語{i}」", "synopsis": f"「のび太」のお話{i}。\n次の段落。", "image_file": f"941_{i}.jpg"}
            for i in (1, 2)
        ],
    }


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "metadata.json"
    meta.save_sources(path, sources())
    return path


def translate(path, story_id="941_1"):
    story = meta.get_story(meta.load_metadata(path), story_id)
    digest = meta.source_hash(story)
    meta.save_translation(path, story_id, digest, "中文标题", "第一段。\n\n第二段。")
    meta.save_tags(path, story_id, digest, "中文标题", "第一段。\n\n第二段。", [f"标签{i}" for i in range(11)])
    return digest


def test_schema_defaults_and_title_normalization(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["episode"] == "0941"
    assert data["broadcast_date"] == "2026-11-07"
    assert data["episode_title"] == "ドラえもん"
    story = data["stories"][0]
    assert story["story_id"] == "941_1" and story["story_index"] == 1
    assert story["title"] == "物語1"
    assert "「のび太」" in story["synopsis"]
    assert story["zh"] is None
    assert story["publish"] == {
        "title": None,
        "video_path": "941_1_final.mp4",
        "douyin": {"tags": [], "cover_portrait_path": None, "cover_landscape_path": None},
        "bilibili": {"tags": [], "tid": 24},
    }


@pytest.mark.parametrize("missing", [False, True])
def test_unset_video_paths_default_to_story_id_and_are_persisted(path, missing):
    data = meta.load_metadata(path)
    data["stories"].reverse()
    for story in data["stories"]:
        if missing:
            story["publish"].pop("video_path")
        else:
            story["publish"]["video_path"] = None
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    meta.update_metadata(path, lambda data: None)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [s["publish"]["video_path"] for s in saved["stories"]] == ["941_2_final.mp4", "941_1_final.mp4"]


@pytest.mark.parametrize("filename", ["941_1_origin.srt", "0941_1_origin.srt"])
def test_story_lookup_is_independent_of_array_order(path, filename):
    data = meta.load_metadata(path)
    data["stories"].reverse()
    assert meta.story_for_subtitle(data, Path(filename))["title"] == "物語1"


@pytest.mark.parametrize("filename", ["942_1_origin.srt", "941_3_origin.srt", "941_origin.srt", "941_0_origin.srt"])
def test_invalid_subtitle_identity_is_rejected(path, filename):
    with pytest.raises(meta.MetadataError):
        meta.story_for_subtitle(meta.load_metadata(path), Path(filename))


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(schema_version=3),
        lambda d: d.update(schema_version=True),
        lambda d: d.update(episode="abc"),
        lambda d: d.update(stories=[]),
        lambda d: d["stories"][0].update(story_id="942_1"),
        lambda d: d["stories"][0].update(story_index=True),
        lambda d: d["stories"][0].update(zh="bad"),
        lambda d: d["stories"].append(copy.deepcopy(d["stories"][0])),
    ],
)
def test_invalid_metadata_is_rejected(path, change):
    data = meta.load_metadata(path)
    change(data)
    with pytest.raises(meta.MetadataError):
        meta.normalize_metadata(data)


def test_refetch_preserves_translation_manual_paths_and_unknown_fields(path):
    digest = translate(path)

    def edit(data):
        data["custom"] = "root value"
        story = data["stories"][0]
        story["custom"] = {"keep": True}
        story["publish"]["video_path"] = "final.mp4"
        story["publish"]["title"] = "哆啦a梦941 人工标题"
        story["publish"]["douyin"]["cover_portrait_path"] = "portrait.jpg"
        story["publish"]["douyin"]["cover_landscape_path"] = "landscape.jpg"

    before = meta.update_metadata(path, edit)
    fresh = sources()
    data = meta.save_sources(path, fresh)
    assert data == before
    fresh = sources()
    fresh["stories"][0]["synopsis"] += "更新。"
    updated = meta.save_sources(path, fresh)
    story = updated["stories"][0]
    assert not meta.translation_is_current(story)
    assert story["zh"]["source_hash"] == digest
    assert story["custom"] == {"keep": True}
    assert updated["custom"] == "root value"
    assert story["publish"] == before["stories"][0]["publish"]


def test_stale_inference_cannot_overwrite_updated_source(path):
    digest = meta.source_hash(meta.load_metadata(path)["stories"][0])
    fresh = sources()
    fresh["stories"][0]["synopsis"] = "新しい話。"
    meta.save_sources(path, fresh)
    before = path.read_bytes()
    with pytest.raises(meta.MetadataError, match="过期翻译"):
        meta.save_translation(path, "941_1", digest, "旧结果", "旧简介")
    assert path.read_bytes() == before


def test_updates_are_per_story_and_keep_newlines(path):
    translate(path, "941_1")
    first = meta.load_metadata(path)["stories"][0]
    translate(path, "941_2")
    data = meta.load_metadata(path)
    assert data["stories"][0] == first
    assert data["stories"][1]["zh"]["synopsis"] == "第一段。\n\n第二段。"
    assert len(first["publish"]["douyin"]["tags"]) == 5
    assert len(first["publish"]["bilibili"]["tags"]) == 11
    assert first["publish"]["title"] == "哆啦a梦941 中文标题"


def test_atomic_replace_failure_preserves_original(path, monkeypatch):
    before = path.read_bytes()
    files = set(path.parent.iterdir())
    monkeypatch.setattr(meta.os, "replace", Mock(side_effect=OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        meta.update_metadata(path, lambda d: d.update(custom="new"))
    assert path.read_bytes() == before
    assert set(path.parent.iterdir()) == files


def test_tag_recovery_repairs_invalid_platform_only(path):
    digest = translate(path)

    def edit(data):
        publish = data["stories"][0]["publish"]
        publish["douyin"]["tags"] = ["#invalid"]
        publish["bilibili"]["tags"] = ["人工标签"]

    meta.update_metadata(path, edit)
    meta.save_tags(path, "941_1", digest, "中文标题", "第一段。\n\n第二段。", ["新标签"])
    publish = meta.load_metadata(path)["stories"][0]["publish"]
    assert publish["douyin"]["tags"] == ["新标签"]
    assert publish["bilibili"]["tags"] == ["人工标签"]


def test_new_translation_invalidates_tags_and_rejects_stale_tag_result(path):
    digest = translate(path)
    meta.save_translation(path, "941_1", digest, "新标题", "新简介")
    story = meta.load_metadata(path)["stories"][0]
    assert story["publish"]["title"] == "哆啦a梦941 新标题"
    assert story["publish"]["douyin"]["tags"] == []
    with pytest.raises(meta.MetadataError, match="过期标签"):
        meta.save_tags(path, "941_1", digest, "中文标题", "第一段。\n\n第二段。", ["旧标签"])


def test_relative_and_absolute_assets_use_metadata_directory(path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)
    assert meta.resolve_asset(path, "covers/portrait.jpg") == tmp_path / "covers/portrait.jpg"
    absolute = tmp_path / "external.mp4"
    assert meta.resolve_asset(path, str(absolute)) == absolute
