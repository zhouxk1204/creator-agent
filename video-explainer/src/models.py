"""数据模型：视频信息、检测器配置、分镜。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoInfo:
    """ffprobe 探测到的源视频信息。"""

    path: Path
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float  # 秒
    codec: str


@dataclass(frozen=True)
class DetectorConfig:
    """分镜检测器配置。type 目前只有 "content"，未来可扩展 adaptive/hash/ai。"""

    type: str = "content"
    threshold: float = 27.0
    min_scene_len_frames: int = 12

    def min_scene_len_seconds(self, fps: float) -> float:
        """min_scene_len（帧）换算成秒，便于日志展示。"""
        if fps <= 0:
            return 0.0
        return self.min_scene_len_frames / fps


@dataclass
class Scene:
    """一个分镜。时间一律使用秒 + 浮点数，同时保存帧号与格式化时间码。"""

    scene_id: str  # "0001"
    start: float  # 秒
    end: float  # 秒
    duration: float  # 秒
    start_frame: int
    end_frame: int
    start_timecode: str  # "00:00:00.000"
    end_timecode: str
    video: str = ""  # 相对路径，如 "scenes/0001.mp4"

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_timecode": self.start_timecode,
            "end_timecode": self.end_timecode,
            "video": self.video,
        }
