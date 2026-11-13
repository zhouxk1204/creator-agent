"""Doraemon episode metadata: story identity, source-safe updates and local assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MetadataError(ValueError):
    pass


def source_hash(story: dict) -> str:
    source = {key: story[key] for key in ("title", "synopsis")}
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def metadata_path(target: Path) -> Path:
    target = Path(target).expanduser().resolve()
    return target if target.name == "metadata.json" else target / "metadata.json"


def normalize_metadata(data: Any) -> dict:
    if not isinstance(data, dict):
        raise MetadataError("metadata.json 必须是 JSON 对象")
    version = data.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise MetadataError(f"不支持 metadata schema_version={version}")
    episode = str(data.get("episode", ""))
    if not episode.isdigit() or int(episode) <= 0:
        raise MetadataError("metadata episode 必须是正整数集号")
    data["episode"] = f"{int(episode):04d}"
    stories = data.get("stories")
    if not isinstance(stories, list) or not stories:
        raise MetadataError("metadata stories 必须是非空数组")
    seen = set()
    for position, story in enumerate(stories, 1):
        if not isinstance(story, dict):
            raise MetadataError("metadata story 必须是对象")
        if version == 1:
            story.setdefault("story_index", position)
            story.setdefault("story_id", f"{int(episode)}_{position}")
        index = story.get("story_index")
        if type(index) is not int or index <= 0:
            raise MetadataError("story_index 必须是正整数")
        expected_id = f"{int(episode)}_{index}"
        if story.get("story_id") != expected_id or expected_id in seen:
            raise MetadataError(f"故事标识不匹配或重复：{story.get('story_id')}")
        seen.add(expected_id)
        for key in ("title", "synopsis"):
            if not isinstance(story.get(key), str):
                raise MetadataError(f"{expected_id}.{key} 必须是字符串")
        story["title"] = story["title"].replace("「", "").replace("」", "")
        story.pop("image", None)
        story.setdefault("zh", None)
        if story["zh"] is not None and not isinstance(story["zh"], dict):
            raise MetadataError(f"{expected_id}.zh 必须是对象或 null")
        publish = story.setdefault("publish", {})
        if not isinstance(publish, dict):
            raise MetadataError(f"{expected_id}.publish 必须是对象")
        publish.setdefault("title", None)
        if publish.get("video_path") is None:
            publish["video_path"] = f"{expected_id}_final.mp4"
        for platform in ("douyin", "bilibili"):
            if not isinstance(publish.setdefault(platform, {}), dict):
                raise MetadataError(f"{expected_id}.publish.{platform} 必须是对象")
            publish[platform].setdefault("tags", [])
        publish["douyin"].setdefault("cover_portrait_path", None)
        publish["douyin"].setdefault("cover_landscape_path", None)
        publish["bilibili"].setdefault("tid", 24)
    if isinstance(data.get("episode_title"), str):
        data["episode_title"] = data["episode_title"].replace("「", "").replace("」", "")
    date = data.get("broadcast_date", "")
    if isinstance(date, str):
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日放送", date)
        if match:
            data["broadcast_date"] = f"{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}"
    data["schema_version"] = 2
    return data


def load_metadata(path: Path) -> dict:
    try:
        return normalize_metadata(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MetadataError(f"无法读取 {path}：{exc}") from exc


def get_story(data: dict, story_id: str) -> dict:
    for story in data["stories"]:
        if story["story_id"] == story_id:
            return story
    raise MetadataError(f"metadata 中没有故事 {story_id}")


def story_for_subtitle(data: dict, origin: Path) -> dict:
    match = re.fullmatch(r"(\d+)_([1-9]\d*)_origin\.srt", origin.name)
    if not match or int(match[1]) != int(data["episode"]):
        raise MetadataError(f"字幕文件名与 metadata 集号不匹配：{origin.name}")
    return get_story(data, f"{int(match[1])}_{int(match[2])}")


def translation_is_current(story: dict) -> bool:
    zh = story.get("zh")
    return (
        isinstance(zh, dict)
        and zh.get("source_hash") == source_hash(story)
        and all(isinstance(zh.get(key), str) and zh[key].strip() for key in ("title", "synopsis"))
    )


def validate_tags(tags: Any, maximum: int) -> list[str]:
    if not isinstance(tags, list) or not tags or len(tags) > maximum:
        raise MetadataError(f"标签必须是 1~{maximum} 个字符串组成的数组")
    if any(not isinstance(tag, str) or not tag.strip() or re.search(r"[#\s,，]", tag) for tag in tags):
        raise MetadataError("标签不能为空或包含 #、空格、逗号")
    if len(set(tags)) != len(tags) or any(len(tag) > 20 for tag in tags):
        raise MetadataError("标签不能重复，每个标签最多 20 字")
    return tags


def resolve_asset(path: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise MetadataError("素材路径未填写")
    asset = Path(value).expanduser()
    return (asset if asset.is_absolute() else path.parent / asset).resolve()


@contextmanager
def _metadata_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(".metadata.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, data: dict) -> None:
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def update_metadata(path: Path, update: Callable[[dict], None]) -> dict:
    with _metadata_lock(path):
        data = load_metadata(path)
        update(data)
        _atomic_write(path, data)
        return data


def save_sources(path: Path, fresh: dict) -> dict:
    fresh = normalize_metadata(fresh)
    with _metadata_lock(path):
        old = load_metadata(path) if path.exists() else {}
        if old and old["episode"] != fresh["episode"]:
            raise MetadataError("不能用其他集号覆盖现有 metadata")
        old_stories = {story["story_id"]: story for story in old.get("stories", [])}
        merged = []
        for source in fresh["stories"]:
            story = old_stories.get(source["story_id"], {}).copy()
            for key in ("story_id", "story_index", "title", "synopsis", "copyright", "image_file"):
                if key in source:
                    story[key] = source[key]
                else:
                    story.pop(key, None)
            merged.append(story)
        data = {**old, **{key: value for key, value in fresh.items() if key != "stories"}, "stories": merged}
        data = normalize_metadata(data)
        _atomic_write(path, data)
        return data


def save_translation(path: Path, story_id: str, expected_hash: str, title: str, synopsis: str) -> dict:
    if not isinstance(title, str) or not title.strip() or "\n" in title or "\r" in title:
        raise MetadataError("中文标题必须是非空单行文本")
    if not isinstance(synopsis, str) or not synopsis.strip():
        raise MetadataError("中文简介不能为空")

    def apply(data: dict) -> None:
        story = get_story(data, story_id)
        if source_hash(story) != expected_hash:
            raise MetadataError(f"{story_id} 日文来源已变化，拒绝写入过期翻译，请重跑")
        previous = story.get("zh") or {}
        previous_title = f"哆啦a梦{int(data['episode'])} {previous.get('title', '')}"
        story["zh"] = {
            **previous,
            "title": title,
            "synopsis": synopsis,
            "source_hash": expected_hash,
            "translated_at": datetime.now(timezone.utc).isoformat(),
        }
        publish = story["publish"]
        if not publish["title"] or publish["title"] == previous_title:
            publish["title"] = f"哆啦a梦{int(data['episode'])} {title}"
        # New/refreshed translations invalidate tags before the next model call.
        for platform in ("douyin", "bilibili"):
            publish[platform]["tags"] = []

    return update_metadata(path, apply)


def save_tags(path: Path, story_id: str, expected_hash: str, title: str, synopsis: str, tags: list[str]) -> dict:
    validate_tags(tags, 11)

    def apply(data: dict) -> None:
        story = get_story(data, story_id)
        if (
            source_hash(story) != expected_hash
            or not translation_is_current(story)
            or story["zh"]["title"] != title
            or story["zh"]["synopsis"] != synopsis
        ):
            raise MetadataError(f"{story_id} 简介已变化，拒绝写入过期标签，请重跑")
        for platform, maximum in (("douyin", 5), ("bilibili", 11)):
            config = story["publish"][platform]
            try:
                validate_tags(config["tags"], maximum)
            except MetadataError:
                config["tags"] = tags[:maximum]

    return update_metadata(path, apply)
