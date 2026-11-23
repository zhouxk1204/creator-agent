"""Per-video upload receipts stored atomically with the episode metadata.

A receipt belongs to a story, platform and account, and identifies the bytes
actually uploaded. Paths are informational, so moving the materials directory
or renaming an unchanged export does not cause a duplicate publication.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .metadata import MetadataError, get_story, update_metadata


def video_hash(video: Path) -> str:
    with video.open("rb") as handle:
        return "sha256:" + hashlib.file_digest(handle, "sha256").hexdigest()


def _receipts(story: dict, platform: str) -> list[dict]:
    receipts = story["publish"][platform].get("uploads", [])
    if not isinstance(receipts, list) or any(
        not isinstance(receipt, dict)
        or receipt.get("status") != "success"
        or not isinstance(receipt.get("account"), str)
        or not isinstance(receipt.get("video_sha256"), str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", receipt["video_sha256"]) is None
        for receipt in receipts
    ):
        raise MetadataError(f"{story['story_id']} 的 {platform} 上传记录损坏，请人工核对，禁止盲目重试")
    return receipts


def was_uploaded(story: dict, platform: str, account: str, digest: str) -> bool:
    return any(
        receipt["account"] == account and receipt["video_sha256"] == digest for receipt in _receipts(story, platform)
    )


def pending_videos(data: dict, videos: list[tuple[str, Path]], platform: str, account: str) -> list[tuple[str, Path]]:
    """Filter receipts before attempting to regenerate upload-only resources."""
    pending = []
    for story_id, video in videos:
        if was_uploaded(get_story(data, story_id), platform, account, video_hash(video)):
            print(f"[跳过] {platform} {story_id} / {video.name}：账号 {account} 已上传成功")
        else:
            pending.append((story_id, video))
    return pending


def record_upload(
    path: Path,
    story_id: str,
    platform: str,
    account: str,
    video: Path,
    digest: str,
    title: str,
    schedule: str | None,
) -> None:
    """Write a receipt only after the uploader reports successful submission."""
    try:
        video_path = video.resolve().relative_to(path.parent.resolve()).as_posix()
    except ValueError:
        video_path = str(video.resolve())

    def apply(data: dict) -> None:
        story = get_story(data, story_id)
        if was_uploaded(story, platform, account, digest):
            return
        receipts = _receipts(story, platform)
        receipts.append(
            {
                "status": "success",
                "account": account,
                "video_path": video_path,
                "video_sha256": digest,
                "title": title,
                "schedule": schedule,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        story["publish"][platform]["uploads"] = receipts

    update_metadata(path, apply)
