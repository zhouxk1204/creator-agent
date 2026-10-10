"""Extraction orchestrator: scenes.json + story video -> candidate frames.

Frames are ALWAYS read from the original story video at the timestamps in
scenes.json (never from the per-scene MP4s). A scene that fails to decode is
recorded as decode_failed and the run continues with the next scene.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from .frame_filter import STATUS_DECODE_FAILED, STATUS_PASSED, check_frame
from .frame_sampler import planned_count, sample_times
from .manifest import CandidateRecord, load_manifest, manifest_path, merge_extraction, save_manifest
from .naming import format_candidate_id
from .scene_reader import EpisodeScenes


@dataclass
class ExtractStats:
    scenes_total: int = 0
    planned: int = 0
    passed: int = 0
    filtered: dict[str, int] = field(default_factory=dict)  # status -> count
    fully_filtered_scenes: list[str] = field(default_factory=list)  # all candidates filtered out

    def bump(self, status: str) -> None:
        self.filtered[status] = self.filtered.get(status, 0) + 1


def inbox_dir(library_root: Path, episode_id: str) -> Path:
    return library_root / "inbox" / episode_id


def extract_episode(ep: EpisodeScenes, library_root: Path) -> tuple[dict[str, CandidateRecord], ExtractStats]:
    """Extract candidate frames for one episode and update its manifest.

    Idempotent: image content is deterministic (same video + same timestamps),
    and merge_extraction preserves any human classification from prior runs.
    """
    stats = ExtractStats(scenes_total=len(ep.scenes))
    out_dir = inbox_dir(library_root, ep.episode_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(ep.video_path))
    if not cap.isOpened():
        raise RuntimeError(f"无法用 OpenCV 打开故事视频: {ep.video_path}")
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None

    new_records: list[CandidateRecord] = []
    try:
        for scene in ep.scenes:
            fps = scene.fps or ep.fps
            times = sample_times(scene.start, scene.end)
            stats.planned += len(times)
            kept_sigs: list = []
            scene_passed = 0
            for ordinal, t in enumerate(times, 1):
                timestamp_ms = int(round(t * 1000))
                cid = format_candidate_id(ep.episode_id, scene.scene_id, timestamp_ms, ordinal)
                rel_image = f"inbox/{ep.episode_id}/{cid}.jpg"
                frame_no = min(int(round(t * fps)), (total_frames - 1) if total_frames else 2**31 - 1)
                try:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
                    ok, frame = cap.read()
                except Exception as e:  # decoder hiccup: record, continue
                    ok, frame = False, None
                    print(f"  !! {scene.scene_id} 第 {ordinal} 帧解码异常: {e}", file=sys.stderr)
                if not ok or frame is None:
                    stats.bump(STATUS_DECODE_FAILED)
                    new_records.append(
                        CandidateRecord(
                            candidate_id=cid,
                            episode_id=ep.episode_id,
                            scene_id=scene.scene_id,
                            timestamp_ms=timestamp_ms,
                            image_path="",
                            quality_status=STATUS_DECODE_FAILED,
                            filter_reason=f"frame_no={frame_no} 读取失败",
                        )
                    )
                    continue
                result = check_frame(frame, kept_sigs)
                if result.status == STATUS_PASSED:
                    kept_sigs.append(result.signature)
                    image_path = out_dir / f"{cid}.jpg"
                    if not cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                        stats.bump(STATUS_DECODE_FAILED)
                        new_records.append(
                            CandidateRecord(
                                candidate_id=cid,
                                episode_id=ep.episode_id,
                                scene_id=scene.scene_id,
                                timestamp_ms=timestamp_ms,
                                image_path="",
                                quality_status=STATUS_DECODE_FAILED,
                                filter_reason="jpg 写入失败",
                            )
                        )
                        continue
                    stats.passed += 1
                    scene_passed += 1
                    new_records.append(
                        CandidateRecord(
                            candidate_id=cid,
                            episode_id=ep.episode_id,
                            scene_id=scene.scene_id,
                            timestamp_ms=timestamp_ms,
                            image_path=rel_image,
                            quality_status=STATUS_PASSED,
                        )
                    )
                else:
                    stats.bump(result.status)
                    new_records.append(
                        CandidateRecord(
                            candidate_id=cid,
                            episode_id=ep.episode_id,
                            scene_id=scene.scene_id,
                            timestamp_ms=timestamp_ms,
                            image_path="",
                            quality_status=result.status,
                            filter_reason=result.reason,
                        )
                    )
            if scene_passed == 0:
                # All candidates filtered/decode-failed: record the scene, don't drop it.
                stats.fully_filtered_scenes.append(scene.scene_id)
    finally:
        cap.release()

    mpath = manifest_path(library_root, ep.episode_id)
    old = load_manifest(mpath)
    merged, report = merge_extraction(old, new_records)
    save_manifest(mpath, merged)
    if report.preserved_reviewed:
        print(f"  保留人工分类 {report.preserved_reviewed} 条（重跑不覆盖）")
    if report.kept_missing:
        print(
            f"  警告: {len(report.kept_missing)} 条已确认记录未出现在本次提取中，已保留: "
            + ", ".join(report.kept_missing[:5])
            + (" ..." if len(report.kept_missing) > 5 else ""),
            file=sys.stderr,
        )
    return merged, stats


def plan_episode(ep: EpisodeScenes) -> list[tuple[str, float, int, list[float]]]:
    """(scene_id, duration, count, times) per scene — for --preview output."""
    return [
        (scene.scene_id, scene.duration, planned_count(scene.duration), sample_times(scene.start, scene.end))
        for scene in ep.scenes
    ]
