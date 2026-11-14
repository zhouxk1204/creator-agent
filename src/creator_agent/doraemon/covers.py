"""Douyin cover: naming, gating and metadata write-back for both orientations.

The covers themselves are rendered by ``jianying_cover`` (剪映 GUI automation).
This module holds the parts that must stay testable without Windows or 剪映:
which stories may be rendered, where the files go, and how the paths are written
back into ``metadata.json``.
"""

from __future__ import annotations

from pathlib import Path

from .metadata import (
    MetadataError,
    get_story,
    load_metadata,
    resolve_asset,
    translation_is_current,
    update_metadata,
)

PORTRAIT_SUFFIX = "_douyin_portrait.jpg"
LANDSCAPE_SUFFIX = "_douyin_landscape.jpg"
ORIENTATIONS = ("portrait", "landscape")


def cover_filename(story_id: str, orientation: str = "portrait") -> str:
    if orientation == "landscape":
        return f"{story_id}{LANDSCAPE_SUFFIX}"
    return f"{story_id}{PORTRAIT_SUFFIX}"


def portrait_filename(story_id: str) -> str:
    return cover_filename(story_id, "portrait")


def landscape_filename(story_id: str) -> str:
    return cover_filename(story_id, "landscape")


def cover_field(orientation: str) -> str:
    return "cover_landscape_path" if orientation == "landscape" else "cover_portrait_path"


def cover_title(story: dict) -> str:
    """The Chinese story title that goes on the cover, or a clear refusal.

    The title is written by ``02``/``06`` translation; without a current
    translation there is nothing sensible to render, and silently falling back
    to the Japanese title would ship a wrong cover.
    """
    if not translation_is_current(story):
        raise MetadataError(f"{story['story_id']} 中文翻译缺失或已过期，请先运行 02 或 06")
    title = (story.get("zh") or {}).get("title") or ""
    title = title.strip()
    if not title:
        raise MetadataError(f"{story['story_id']} 中文标题为空，无法生成封面")
    if "\n" in title or "\r" in title:
        raise MetadataError(f"{story['story_id']} 中文标题含换行，封面只支持单行")
    return title


def existing_cover(metadata_file: Path, story: dict, orientation: str = "portrait") -> Path | None:
    """The cover currently recorded in metadata, if the file really exists."""
    value = (story.get("publish") or {}).get("douyin", {}).get(cover_field(orientation))
    if not isinstance(value, str) or not value.strip():
        return None
    asset = resolve_asset(metadata_file, value)
    return asset if asset.is_file() else None


def set_cover(
    metadata_file: Path, story_id: str, cover: Path | str, *, orientation: str = "portrait", overwrite: bool = False
) -> dict:
    """Record a cover path, relative to the metadata directory.

    Existing paths are only replaced when ``overwrite`` is set or the recorded
    file is gone — a hand-made cover must survive an accidental re-run.
    """
    metadata_file = Path(metadata_file)
    cover_path = Path(cover)
    if not cover_path.is_absolute():
        cover_path = metadata_file.parent / cover_path
    cover_path = cover_path.resolve()
    if not cover_path.is_file():
        raise MetadataError(f"封面文件不存在：{cover_path}")
    try:
        stored = cover_path.relative_to(metadata_file.parent.resolve()).as_posix()
    except ValueError:
        stored = cover_path.as_posix()
    field = cover_field(orientation)

    def apply(data: dict) -> None:
        story = get_story(data, story_id)
        douyin = story["publish"]["douyin"]
        current = douyin.get(field)
        if current and not overwrite:
            current_asset = resolve_asset(metadata_file, current)
            if current_asset.is_file() and current_asset.resolve() != cover_path:
                raise MetadataError(
                    f"{story_id} 已有自定义{'横' if orientation == 'landscape' else '竖'}封面 "
                    f"{current}，加 --force 才会覆盖"
                )
        douyin[field] = stored

    return update_metadata(metadata_file, apply)


def set_portrait_cover(metadata_file: Path, story_id: str, cover: Path | str, *, overwrite: bool = False) -> dict:
    return set_cover(metadata_file, story_id, cover, orientation="portrait", overwrite=overwrite)


def set_landscape_cover(metadata_file: Path, story_id: str, cover: Path | str, *, overwrite: bool = False) -> dict:
    return set_cover(metadata_file, story_id, cover, orientation="landscape", overwrite=overwrite)


def stories_to_render(data: dict, requested: list[str] | None) -> list[dict]:
    if requested:
        return [get_story(data, story_id) for story_id in requested]
    return list(data["stories"])


def cover_plan(
    metadata_file: Path, story: dict, *, force: bool, orientation: str = "portrait"
) -> tuple[Path, str] | None:
    """Decide whether this story needs a render; returns ``(target, title)``."""
    title = cover_title(story)
    current = existing_cover(metadata_file, story, orientation)
    if current is not None and not force:
        return None
    target = metadata_file.parent / cover_filename(story["story_id"], orientation)
    return target, title


def load(path: Path) -> dict:
    return load_metadata(Path(path))
