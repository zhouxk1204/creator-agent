"""数据模型：视频信息、检测器配置、后处理配置、分镜。"""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class PostProcessConfig:
    """检测后处理配置：闪烁误切合并 + 黑场标记。

    所有阈值都在这里显式可调，不写死在代码里；阈值只在
    scene_splitter._hsv_frame_diff 当前实现的度量口径下有意义
    （半分辨率 + HSV 三通道 mean-abs-diff 之和）。
    """

    # ---- 闪烁误切合并 ----
    merge_enabled: bool = True
    merge_threshold: float = 100.0  # 跨边界最小相似度低于该值 → 合并候选
    cross_window: int = 6  # 边界两侧各检查的帧数
    analysis_scale: float = 0.5  # 分析用缩放比例（半分辨率）
    merge_max_iterations: int = 4  # 合并迭代上限（通常第 2 轮即收敛）

    # ---- 黑场标记（只标记不删除，保持时间轴完整供字幕匹配） ----
    black_enabled: bool = True
    black_max_yavg: float = 20.0  # 帧平均亮度上限（limited-range 纯黑 ≈ 16）
    black_max_ystd: float = 8.0  # 帧亮度标准差上限（防止把有内容的暗场误判为黑场）
    black_min_pblack: float = 0.95  # 帧内黑像素占比下限
    black_pixel_threshold: int = 32  # 灰度低于此值计为黑像素
    black_sample_frames: int = 8  # 每个分镜最多采样帧数
    black_min_frame_ratio: float = 0.95  # 采样帧中被判黑的比例下限

    # ---- 试运行：只出分析报告，不改分镜结果 ----
    dry_run: bool = False

    def needs_analysis(self) -> bool:
        """是否需要额外的逐帧分析 pass（合并和黑场都关则跳过）。"""
        return self.merge_enabled or self.black_enabled


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
    # ---- 后处理审计字段（合并/黑场标记，便于复盘） ----
    black: bool = False  # 纯黑场标记（仍导出、不删除，保持时间轴完整）
    merge_history: list[dict] = field(default_factory=list)  # 被合并进来的原始分镜及原因
    boundary_score: float | None = None  # 本分镜起始边界的 min_cross 复核分数（首个分镜为 None）
    review_status: str = "auto"  # auto = 自动处理；人工确认后由下游改为 manual

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
            "black": self.black,
            "merge_history": self.merge_history,
            "boundary_score": self.boundary_score,
            "review_status": self.review_status,
        }
