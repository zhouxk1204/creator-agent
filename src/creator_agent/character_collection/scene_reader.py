"""Read ``scenes.json`` produced by scripts/split_scene.py into typed records.

Timestamps in scenes.json are RELATIVE TO THE STORY VIDEO (e.g. ``935_1.mp4``),
not the original broadcast file. Frame extraction must therefore seek in the
story video, never in the per-scene MP4s.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SceneRecord:
    scene_id: str  # e.g. "V003", taken verbatim from scenes.json
    index: int
    start: float  # seconds, relative to the story video
    end: float
    duration: float
    fps: float


@dataclass(frozen=True)
class EpisodeScenes:
    episode_id: str  # story video stem, e.g. "935_1"
    video_path: Path  # the ORIGINAL story video (frame source)
    scenes_json: Path
    fps: float
    scenes: list[SceneRecord]


class SceneReaderError(Exception):
    pass


def resolve_video_path(scenes_json: Path, source_name: str, video_override: Path | None) -> Path:
    """Locate the story video for a scenes.json.

    Layout from split_scene.py is ``output/<stem>_scenes/scenes.json`` with the
    story video as the sibling ``output/<stem>.mp4``. We try (in order):
    explicit override, sibling of the scenes dir, next to the scenes.json.
    """
    candidates: list[Path] = []
    if video_override is not None:
        candidates.append(video_override)
    scenes_dir = scenes_json.parent
    candidates.append(scenes_dir.parent / source_name)
    candidates.append(scenes_dir / source_name)
    for c in candidates:
        if c.is_file():
            return c
    tried = "\n  ".join(str(c) for c in candidates)
    raise SceneReaderError(f"找不到故事视频 {source_name}，尝试过:\n  {tried}\n可用 --video 显式指定。")


def _parse_source(doc: dict) -> tuple[str, float]:
    """Return (source video filename, fps declared at source level).

    split_scene.py writes ``"source": "935_1.mp4"`` (v1.0); other splitters
    write a dict like ``"source": {"file": "test.mp4", "fps": 30.0, ...}``
    (v1.1). Both are accepted.
    """
    source = doc.get("source")
    if isinstance(source, str) and source:
        return source, 0.0
    if isinstance(source, dict):
        name = source.get("file")
        if isinstance(name, str) and name:
            return name, float(source.get("fps") or 0.0)
    raise SceneReaderError("scenes.json 缺少 source 字段（应为视频文件名字符串或含 file 字段的对象）")


def load_episode_scenes(scenes_json: Path, video_override: Path | None = None) -> EpisodeScenes:
    """Parse scenes.json and resolve its story video. Raises SceneReaderError."""
    if not scenes_json.is_file():
        raise SceneReaderError(f"scenes.json 不存在: {scenes_json}")
    try:
        doc = json.loads(scenes_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SceneReaderError(f"scenes.json 解析失败 {scenes_json}: {e}") from e

    source_name, source_fps = _parse_source(doc)
    raw_scenes = doc.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise SceneReaderError(f"scenes.json 没有 scenes 列表: {scenes_json}")

    video_path = resolve_video_path(scenes_json, source_name, video_override)
    episode_id = Path(source_name).stem
    fps = float(doc.get("fps") or 0.0) or source_fps

    scenes: list[SceneRecord] = []
    for i, s in enumerate(raw_scenes, 1):
        try:
            start = float(s["start"])
            end = float(s["end"])
            scene_fps = float(s.get("fps") or 0.0) or fps
        except (KeyError, TypeError, ValueError) as e:
            raise SceneReaderError(f"scenes.json 第 {i} 个分镜字段异常: {s!r} ({e})") from e
        if end <= start:
            raise SceneReaderError(f"scenes.json 第 {i} 个分镜时间区间无效: start={start} end={end}")
        scenes.append(
            SceneRecord(
                # v1.0 uses "id" ("V003"); v1.1 uses "scene_id" ("0003").
                scene_id=str(s.get("id") or s.get("scene_id") or f"V{i:03d}"),
                index=int(s.get("index", i)),
                start=start,
                end=end,
                duration=float(s.get("duration") or round(end - start, 3)),
                fps=scene_fps,
            )
        )
    if fps <= 0:
        fps = scenes[0].fps
    return EpisodeScenes(
        episode_id=episode_id,
        video_path=video_path,
        scenes_json=scenes_json,
        fps=fps,
        scenes=scenes,
    )


def default_scenes_json(episode_id: str, output_root: Path = Path("output")) -> Path:
    """Default location following split_scene.py's output layout."""
    return output_root / f"{episode_id}_scenes" / "scenes.json"
