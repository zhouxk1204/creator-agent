"""核心流程：PySceneDetect 分镜检测 → 后处理（闪烁误切合并 + 黑场标记）→ FFmpeg 切割 → scenes.json。

未来阶段（SceneAnalyzer / SubtitleMatcher / ScriptGenerator）以 scenes.json
作为输入，本模块只做“分镜数据基础设施”。

后处理（可配置，见 PostProcessConfig）：
1. 边界复核合并 —— 动画闪烁特效（明暗帧交替）会让 ContentDetector 在一个
   镜头内部反复误切。对每个候选边界，取两侧各 cross_window 帧做两两
   相似度比较，取最小值 min_cross：闪烁段两侧必存在相近帧（min_cross 小），
   真正的切换两侧画面完全不同（min_cross 大）。低于 merge_threshold 的边界
   撤销（合并），阈值可调。
2. 黑场标记 —— 按平均亮度 / 亮度标准差 / 黑像素占比 / 黑帧比例综合判定，
   只标记 black=true 不删除，分镜照常导出，保证时间轴完整供字幕匹配。
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from models import DetectorConfig, PostProcessConfig, Scene, VideoInfo
from video_utils import (
    FFmpegCommandError,
    VideoProbeError,
    find_ffmpeg,
    find_ffprobe,
    format_timecode,
    probe_video,
    verify_video_file,
)

SCENES_JSON_NAME = "scenes.json"
SCENES_DIR_NAME = "scenes"
LOG_NAME = "scene_split.log"
REPORT_NAME = "postprocess_report.json"
SCENES_JSON_VERSION = "1.1"


class SceneSplitError(RuntimeError):
    """分镜流程中的可预期错误（CLI 捕获后打印明确信息，不抛 traceback）。"""


class SplitLogger:
    """同时输出到控制台和 scene_split.log。

    控制台只打印 "[TAG] message"；完整 traceback 只写入日志文件。
    """

    def __init__(self, log_path: Path) -> None:
        fmt = logging.Formatter("%(message)s")

        file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        file_handler.setFormatter(fmt)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)

        # 主 logger：控制台 + 文件
        self._log = logging.getLogger(f"scene_split.{id(self)}")
        self._log.setLevel(logging.INFO)
        self._log.propagate = False
        self._log.addHandler(console_handler)
        self._log.addHandler(file_handler)

        # 仅文件的 logger：用于完整 traceback
        self._file_only = logging.getLogger(f"scene_split.file.{id(self)}")
        self._file_only.setLevel(logging.INFO)
        self._file_only.propagate = False
        self._file_only.addHandler(file_handler)

    def _emit(self, tag: str, message: str) -> None:
        self._log.info(f"[{tag}] {message}")

    def info(self, message: str) -> None:
        self._emit("INFO", message)

    def scene(self, scene: Scene) -> None:
        self._emit(
            "SCENE",
            f"{scene.scene_id} {scene.start_timecode} → {scene.end_timecode}  duration={scene.duration:.3f}s",
        )

    def export(self, message: str) -> None:
        self._emit("EXPORT", message)

    def boundary(self, message: str) -> None:
        self._emit("BOUNDARY", message)

    def merge(self, message: str) -> None:
        self._emit("MERGE", message)

    def black(self, message: str) -> None:
        self._emit("BLACK", message)

    def verify(self, message: str) -> None:
        self._emit("VERIFY", message)

    def error(self, message: str) -> None:
        self._emit("ERROR", message)

    def exception(self, message: str) -> None:
        """控制台打印简明错误，日志文件追加完整 traceback。"""
        self.error(message)
        self._file_only.error(traceback.format_exc())

    def close(self) -> None:
        """关闭并移除所有 handler。

        Windows 上不关闭 FileHandler 会一直占用 scene_split.log，
        导致同进程内 --overwrite 重跑时无法清理旧日志。
        """
        for logger in (self._log, self._file_only):
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)


@dataclass
class SplitSummary:
    """一次 split 运行的结果汇总。"""

    scenes: list[Scene]
    exported: int
    failed: int
    scenes_json: Path
    log_file: Path

    @property
    def ok(self) -> bool:
        return self.failed == 0


def detect_scenes(video_path: Path, config: DetectorConfig):
    """统一的分镜检测入口。

    目前使用 PySceneDetect 的 ContentDetector；未来替换为
    AdaptiveDetector / HashDetector / AI 检测器时只需修改本函数，
    调用方（SceneSplitter）不受影响。

    返回 list[(start: FrameTimecode, end: FrameTimecode)]，end 为下一分镜起始帧。
    """
    if config.type != "content":
        raise SceneSplitError(f"不支持的检测器类型: {config.type!r}（当前版本只支持 'content'）")
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError as exc:
        raise SceneSplitError("未安装 PySceneDetect，请先执行: pip install scenedetect[opencv]") from exc

    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(
        ContentDetector(
            threshold=config.threshold,
            min_scene_len=config.min_scene_len_frames,
        )
    )
    manager.detect_scenes(video, show_progress=False)
    scene_list = manager.get_scene_list()

    if not scene_list:
        # 没有任何切换点：整个视频作为唯一的分镜（Test 2 的要求）
        scene_list = [(video.base_timecode, video.duration)]
    return scene_list


# ---------------------------------------------------------------- 后处理


def _import_cv2():
    """后处理需要 opencv + numpy；缺失时给出明确错误而不是 traceback。"""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise SceneSplitError(
            "后处理（边界复核/黑场标记）需要 opencv 和 numpy，请先执行: pip install scenedetect[opencv]"
        ) from exc
    return cv2, np


def _hsv_frame_diff(h1, h2, cv2, np) -> float:
    """两帧（已转为 HSV）内容差异：HSV 三通道 mean-abs-diff 之和。

    这是 merge_threshold 标定所用的度量口径，改算法必须重新标定阈值。
    """
    c1 = cv2.split(h1)
    c2 = cv2.split(h2)
    return sum(float(np.mean(np.abs(a.astype(np.int32) - b.astype(np.int32)))) for a, b in zip(c1, c2))


def _build_black_sample_plan(frame_ranges: list[tuple[int, int]], cfg: PostProcessConfig) -> dict[int, list[int]]:
    """每个分镜均匀采样 ≤black_sample_frames 帧（覆盖整个分镜，含首尾附近）。"""
    plan: dict[int, list[int]] = {}
    for idx, (sf, ef) in enumerate(frame_ranges):
        length = ef - sf
        if length <= 0:
            continue
        n = min(cfg.black_sample_frames, length)
        positions = sorted({sf + min(length - 1, round(length * (k + 0.5) / n)) for k in range(n)})
        plan[idx] = positions
    return plan


def _analyze_frames(
    video_path: Path,
    boundaries: list[int],
    sample_plan: dict[int, list[int]],
    cfg: PostProcessConfig,
) -> tuple[dict[int, float | None], dict[int, list[tuple[float, float, float]]]]:
    """单次顺序解码，同时计算：

    - 每个候选边界的 min_cross（边界两侧各 cross_window 帧两两差异的最小值）
    - 每个分镜的黑场采样统计 [(yavg, ystd, pblack), ...]

    全片只解码一遍；滚动缓冲 2*cross_window 帧，内存占用与视频长度无关。
    """
    cv2, np = _import_cv2()
    w = cfg.cross_window

    right_frames: dict[int, list[int]] = defaultdict(list)  # 帧号 → 它是哪些边界的右侧帧
    window_frames: set[int] = set()
    for b in boundaries:
        for n in range(max(0, b - w), b + w):
            window_frames.add(n)
        for n in range(b, b + w):
            right_frames[n].append(b)

    sample_map: dict[int, list[int]] = defaultdict(list)  # 帧号 → 采样它的分镜序号
    for scene_idx, positions in sample_plan.items():
        for n in positions:
            sample_map[n].append(scene_idx)

    needed = window_frames | set(sample_map)
    if not needed:
        return {}, {}

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise SceneSplitError(f"无法打开视频进行后处理分析: {video_path}")

    buf: deque = deque(maxlen=2 * w)  # (帧号, 半分辨率 HSV 图)
    min_cross: dict[int, float] = {b: math.inf for b in boundaries}
    black_stats: dict[int, list[tuple[float, float, float]]] = {i: [] for i in sample_plan}

    idx = -1
    last_needed = max(needed)
    while idx < last_needed:
        ok, frame = cap.read()
        if not ok:
            break  # 可解码帧数少于元数据标称：剩余边界按“证据不足”处理
        idx += 1
        if idx not in needed:
            continue
        half = cv2.resize(frame, None, fx=cfg.analysis_scale, fy=cfg.analysis_scale)

        if idx in sample_map:
            gray = cv2.cvtColor(half, cv2.COLOR_BGR2GRAY)
            stats = (
                float(gray.mean()),
                float(gray.std()),
                float(np.mean(gray < cfg.black_pixel_threshold)),
            )
            for scene_idx in sample_map[idx]:
                black_stats[scene_idx].append(stats)

        hsv = cv2.cvtColor(half, cv2.COLOR_BGR2HSV) if idx in window_frames else None
        if idx in right_frames:
            for b in right_frames[idx]:
                for left_idx, left_hsv in buf:
                    if b - w <= left_idx < b:
                        diff = _hsv_frame_diff(left_hsv, hsv, cv2, np)
                        if diff < min_cross[b]:
                            min_cross[b] = diff
        if hsv is not None:
            buf.append((idx, hsv))
    cap.release()

    # 证据不足（窗口帧没采全）的边界记为 None，调用方按“保留”处理
    return (
        {b: (None if math.isinf(v) else v) for b, v in min_cross.items()},
        black_stats,
    )


@dataclass
class _MergedRange:
    """合并后的分镜区间（帧号 + 秒 + 审计信息）。"""

    start_frame: int
    end_frame: int
    start_sec: float
    end_sec: float
    constituents: list[int]  # 原始分镜序号（0 基）
    merge_history: list[dict]
    boundary_score: float | None  # 起始边界的 min_cross；首个分镜为 None


def _apply_merge_pass(
    frame_ranges: list[tuple[int, int, float, float]],
    min_cross: dict[int, float | None],
    cfg: PostProcessConfig,
    log: SplitLogger,
) -> tuple[list[_MergedRange], list[dict], list[list[int]]]:
    """按 min_cross 阈值撤销闪烁误切边界。

    返回 (合并后区间, 逐边界复核记录, 每轮迭代合并的边界)。
    边界帧号在合并后不变，剩余边界的分数也不变，因此第 1 轮即可收敛；
    仍按配置迭代复查直到没有新的合并候选或达到上限。
    """
    reviews: list[dict] = []
    for b in [sf for sf, _, _, _ in frame_ranges[1:]]:
        score = min_cross.get(b)
        reviews.append(
            {
                "frame": b,
                "timecode": format_timecode(_frames_to_sec(frame_ranges, b)),
                "min_cross": None if score is None else round(score, 2),
                "decision": ("keep" if score is None or score >= cfg.merge_threshold else "merge"),
            }
        )
        verdict = "KEEP" if reviews[-1]["decision"] == "keep" else "MERGE"
        score_text = "n/a(证据不足)" if score is None else f"{score:.1f}"
        log.boundary(f"f{b} {reviews[-1]['timecode']} min_cross={score_text} → {verdict}")

    mergeable: set[int] = set()
    iterations: list[list[int]] = []
    for round_no in range(1, max(1, cfg.merge_max_iterations) + 1):
        candidates = sorted(
            b
            for b, score in min_cross.items()
            if score is not None and score < cfg.merge_threshold and b not in mergeable
        )
        if not candidates:
            if round_no > 1:
                log.merge(f"iteration {round_no}: 无新增候选，收敛")
            break
        mergeable.update(candidates)
        iterations.append(candidates)
        log.merge(f"iteration {round_no}: 撤销 {len(candidates)} 个边界 (min_cross < {cfg.merge_threshold})")
        # 边界帧号与分数不因合并而改变，下一轮必然收敛；
        # 保留迭代结构以支持未来引入会改变的合并规则。
    else:
        log.merge(f"达到迭代上限 {cfg.merge_max_iterations}，停止合并")

    merged: list[_MergedRange] = []
    for idx, (sf, ef, ssec, esec) in enumerate(frame_ranges):
        orig_id = f"{idx + 1:04d}"
        if merged and sf in mergeable:
            prev = merged[-1]
            prev.end_frame = ef
            prev.end_sec = esec
            prev.constituents.append(idx)
            prev.merge_history.append(
                {
                    "absorbed_scene_id": orig_id,
                    "absorbed_start_frame": sf,
                    "absorbed_end_frame": ef,
                    "boundary_frame": sf,
                    "min_cross": (None if min_cross.get(sf) is None else round(min_cross[sf], 2)),
                    "reason": "min_cross_below_merge_threshold",
                }
            )
            log.merge(
                f"原始分镜 {orig_id} 并入 {f'{prev.constituents[0] + 1:04d}'} "
                f"(边界 f{sf}, min_cross={prev.merge_history[-1]['min_cross']})"
            )
        else:
            merged.append(
                _MergedRange(
                    start_frame=sf,
                    end_frame=ef,
                    start_sec=ssec,
                    end_sec=esec,
                    constituents=[idx],
                    merge_history=[],
                    boundary_score=None,
                )
            )

    # 保留下来的边界分数回填到各分镜
    for m in merged[1:]:
        score = min_cross.get(m.start_frame)
        m.boundary_score = None if score is None else round(score, 2)

    return merged, reviews, iterations


def _frames_to_sec(frame_ranges: list[tuple[int, int, float, float]], frame: int) -> float:
    """从帧号近似换算秒（用于日志时间码）。"""
    for sf, ef, ssec, esec in frame_ranges:
        if sf <= frame <= ef and ef > sf:
            return ssec + (frame - sf) * (esec - ssec) / (ef - sf)
    return 0.0


def _judge_black(samples: list[tuple[float, float, float]], cfg: PostProcessConfig) -> tuple[bool, dict]:
    """按平均亮度 + 亮度标准差 + 黑像素占比 + 黑帧比例综合判定纯黑场。

    单帧判黑：yavg < black_max_yavg 且 pblack >= black_min_pblack；
    分镜判黑：黑帧比例 >= black_min_frame_ratio 且平均 ystd <= black_max_ystd。
    亮度标准差约束防止把“黑底带内容”（字幕卡、暗夜场景）误判为纯黑场。
    """
    if not samples:
        return False, {"samples": 0}
    black_frames = sum(1 for yavg, _, pblack in samples if yavg < cfg.black_max_yavg and pblack >= cfg.black_min_pblack)
    ratio = black_frames / len(samples)
    mean_yavg = sum(s[0] for s in samples) / len(samples)
    mean_ystd = sum(s[1] for s in samples) / len(samples)
    mean_pblack = sum(s[2] for s in samples) / len(samples)
    is_black = ratio >= cfg.black_min_frame_ratio and mean_ystd <= cfg.black_max_ystd
    return is_black, {
        "samples": len(samples),
        "black_frame_ratio": round(ratio, 3),
        "mean_yavg": round(mean_yavg, 2),
        "mean_ystd": round(mean_ystd, 2),
        "mean_pblack": round(mean_pblack, 3),
    }


class SceneSplitter:
    """输入原始视频，输出 scenes/*.mp4 + scenes.json + scene_split.log。

    原视频永远只读，绝不移动 / 删除 / 覆盖。
    """

    def __init__(
        self,
        input_video: Path | str,
        output_dir: Path | str | None = None,
        config: DetectorConfig | None = None,
        export: bool = True,
        overwrite: bool = False,
        postprocess: PostProcessConfig | None = None,
    ) -> None:
        self.input_video = Path(input_video)
        self.output_dir = Path(output_dir) if output_dir is not None else self.input_video.with_suffix("")
        self.config = config or DetectorConfig()
        self.postprocess = postprocess or PostProcessConfig()
        self.export = export
        self.overwrite = overwrite
        self.logger: SplitLogger | None = None

    # ------------------------------------------------------------------ run

    def run(self) -> SplitSummary:
        ffmpeg, ffprobe = self._preflight()
        self._prepare_output_dir()
        self.logger = SplitLogger(self.output_dir / LOG_NAME)
        log = self.logger

        try:
            try:
                return self._run_pipeline(ffmpeg, ffprobe, log)
            except SceneSplitError as exc:
                log.exception(str(exc))
                raise
            except Exception as exc:  # 未预期错误：同样转成明确信息 + 文件里留 traceback
                log.exception(f"未预期的错误: {exc}")
                raise SceneSplitError(f"未预期的错误: {exc}") from exc
        finally:
            self.logger.close()

    # ------------------------------------------------------------- pipeline

    def _run_pipeline(self, ffmpeg: Path, ffprobe: Path, log: SplitLogger) -> SplitSummary:
        info = probe_video(self.input_video, ffprobe)
        self._log_header(log, info, ffmpeg)

        log.info("Detecting scenes...")
        raw_scenes = detect_scenes(self.input_video, self.config)
        # (start_frame, end_frame, start_sec, end_sec)，秒数沿用 FrameTimecode 的精确换算
        frame_ranges = [(s.get_frames(), e.get_frames(), s.get_seconds(), e.get_seconds()) for s, e in raw_scenes]
        log.info(f"Detected scenes: {len(frame_ranges)}")

        pp = self.postprocess
        merged, reviews, iterations, black_report = self._postprocess(frame_ranges, log)

        if pp.dry_run and pp.needs_analysis():
            # dry-run：报告照常输出，但分镜结果保持检测原样，不做合并/标记
            log.info("[DRY-RUN] 仅生成后处理报告，分镜结果保持检测原样")
            scenes = self._build_scenes(
                [_MergedRange(sf, ef, ssec, esec, [i], [], None) for i, (sf, ef, ssec, esec) in enumerate(frame_ranges)]
            )
        else:
            scenes = self._build_scenes(merged)
            if pp.black_enabled:
                for scene in scenes:
                    scene.black = scene.scene_id in {s["scene_id"] for s in black_report if s["black"]}

        for scene in scenes:
            log.scene(scene)
        log.info(f"Total scenes after postprocess: {len(scenes)}")

        exported = failed = 0
        if self.export:
            log.info("Starting video extraction...")
            exported, failed = self._export_scenes(scenes, ffmpeg, ffprobe, log)

        scenes_json = self._write_scenes_json(
            info, scenes, original_count=len(frame_ranges), merged_count=sum(len(it) for it in iterations)
        )
        self._write_report(log, frame_ranges, scenes, reviews, iterations, black_report)

        log.info(f"Total scenes: {len(scenes)}\n[INFO] Export success: {exported}\n[INFO] Export failed: {failed}")
        if failed:
            log.error(f"有 {failed} 个分镜导出失败，详见上方 [ERROR] 记录。")
        else:
            log.info("Finished.")

        return SplitSummary(
            scenes=scenes,
            exported=exported,
            failed=failed,
            scenes_json=scenes_json,
            log_file=self.output_dir / LOG_NAME,
        )

    # ------------------------------------------------------------ postprocess

    def _postprocess(
        self,
        frame_ranges: list[tuple[int, int, float, float]],
        log: SplitLogger,
    ) -> tuple[list[_MergedRange], list[dict], list[list[int]], list[dict]]:
        """边界复核合并 + 黑场标记。返回 (合并区间, 逐边界复核, 迭代记录, 黑场报告)。"""
        pp = self.postprocess
        reviews: list[dict] = []
        iterations: list[list[int]] = []
        black_report: list[dict] = []

        if not pp.needs_analysis():
            merged = [
                _MergedRange(sf, ef, ssec, esec, [i], [], None) for i, (sf, ef, ssec, esec) in enumerate(frame_ranges)
            ]
            return merged, reviews, iterations, black_report

        boundaries = [sf for sf, _, _, _ in frame_ranges[1:]] if pp.merge_enabled else []
        sample_plan = (
            _build_black_sample_plan([(sf, ef) for sf, ef, _, _ in frame_ranges], pp) if pp.black_enabled else {}
        )

        log.info(
            f"Postprocess analysis: {len(boundaries)} boundaries, "
            f"{sum(len(v) for v in sample_plan.values())} black samples "
            f"(window={pp.cross_window}, scale={pp.analysis_scale})"
        )
        min_cross, black_stats = _analyze_frames(self.input_video, boundaries, sample_plan, pp)

        # ② 边界复核合并
        if pp.merge_enabled:
            merged, reviews, iterations = _apply_merge_pass(frame_ranges, min_cross, pp, log)
        else:
            merged = [
                _MergedRange(sf, ef, ssec, esec, [i], [], None) for i, (sf, ef, ssec, esec) in enumerate(frame_ranges)
            ]

        # ③ 黑场标记（合并后判定；组成区间全部判黑，合并结果才判黑）
        if pp.black_enabled:
            for pos, m in enumerate(merged):
                samples = [s for ci in m.constituents for s in black_stats.get(ci, [])]
                is_black, detail = _judge_black(samples, pp)
                scene_id = f"{pos + 1:04d}"
                black_report.append({"scene_id": scene_id, "black": is_black, **detail})
                if is_black:
                    log.black(
                        f"{scene_id} mean_yavg={detail['mean_yavg']} "
                        f"mean_ystd={detail['mean_ystd']} mean_pblack={detail['mean_pblack']} "
                        f"black_frame_ratio={detail['black_frame_ratio']} → black=true"
                        "（仍导出并保留在时间轴上，供字幕匹配）"
                    )

        return merged, reviews, iterations, black_report

    # -------------------------------------------------------------- helpers

    def _preflight(self) -> tuple[Path, Path]:
        """开始前的环境检查，任何一项失败都给出明确错误（不抛 traceback 给用户）。"""
        if not self.input_video.is_file():
            raise SceneSplitError(f"输入文件不存在: {self.input_video}")
        if not self.input_video.suffix:
            raise SceneSplitError(f"输入文件没有扩展名，可能不是视频: {self.input_video}")
        try:
            import scenedetect  # noqa: F401
        except ImportError as exc:
            raise SceneSplitError("未安装 PySceneDetect，请先执行: pip install scenedetect[opencv]") from exc
        ffmpeg = self._find_or_raise(find_ffmpeg)
        ffprobe = self._find_or_raise(find_ffprobe)
        return ffmpeg, ffprobe

    @staticmethod
    def _find_or_raise(finder) -> Path:
        try:
            return finder()
        except Exception as exc:
            raise SceneSplitError(str(exc)) from exc

    def _prepare_output_dir(self) -> None:
        """输出目录冲突处理：默认拒绝覆盖，--overwrite 时清理旧产物。"""
        out = self.output_dir
        if out.exists() and any(out.iterdir()):
            if not self.overwrite:
                raise SceneSplitError(
                    f"输出目录已存在且非空: {out}\n默认不会覆盖已有结果；确认要重新生成请加 --overwrite"
                )
            scenes_dir = out / SCENES_DIR_NAME
            if scenes_dir.is_dir():
                shutil.rmtree(scenes_dir)
            for stale in (out / SCENES_JSON_NAME, out / LOG_NAME, out / REPORT_NAME):
                stale.unlink(missing_ok=True)
        out.mkdir(parents=True, exist_ok=True)

    def _log_header(self, log: SplitLogger, info: VideoInfo, ffmpeg: Path) -> None:
        cfg = self.config
        log.info(f"Input: {self.input_video}")
        log.info(f"Output: {self.output_dir}")
        log.info(f"FFmpeg: {ffmpeg}")
        log.info(f"Video resolution: {info.width}x{info.height}")
        log.info(f"FPS: {info.fps}")
        log.info(f"Frames: {info.frame_count}")
        log.info(f"Duration: {info.duration:.3f}s")
        log.info(f"Codec: {info.codec}")
        log.info(f"Detector: {cfg.type}")
        log.info(f"Threshold: {cfg.threshold}")
        log.info(
            f"Min scene length: {cfg.min_scene_len_frames} frames "
            f"(≈{cfg.min_scene_len_seconds(info.fps):.3f}s @ {info.fps}fps)"
        )
        pp = self.postprocess
        log.info(
            f"Postprocess: merge={'on' if pp.merge_enabled else 'off'}"
            f"(threshold={pp.merge_threshold}, window={pp.cross_window}, scale={pp.analysis_scale}), "
            f"black={'on' if pp.black_enabled else 'off'}"
            f"(yavg<{pp.black_max_yavg}, ystd<{pp.black_max_ystd}, pblack>={pp.black_min_pblack}), "
            f"dry_run={pp.dry_run}"
        )

    def _build_scenes(self, merged: list[_MergedRange]) -> list[Scene]:
        scenes: list[Scene] = []
        for index, m in enumerate(merged, start=1):
            scenes.append(
                Scene(
                    scene_id=f"{index:04d}",
                    start=m.start_sec,
                    end=m.end_sec,
                    duration=m.end_sec - m.start_sec,
                    start_frame=m.start_frame,
                    end_frame=m.end_frame,
                    start_timecode=format_timecode(m.start_sec),
                    end_timecode=format_timecode(m.end_sec),
                    video=(f"{SCENES_DIR_NAME}/{index:04d}.mp4" if self.export else ""),
                    merge_history=m.merge_history,
                    boundary_score=m.boundary_score,
                )
            )
        return scenes

    def _export_scenes(
        self,
        scenes: list[Scene],
        ffmpeg: Path,
        ffprobe: Path,
        log: SplitLogger,
    ) -> tuple[int, int]:
        scenes_dir = self.output_dir / SCENES_DIR_NAME
        scenes_dir.mkdir(parents=True, exist_ok=True)
        exported = failed = 0

        for scene in scenes:
            out_path = scenes_dir / f"{scene.scene_id}.mp4"
            log.export(out_path.name)
            try:
                self._cut_scene(ffmpeg, out_path, scene)
                duration = verify_video_file(out_path, ffprobe)
            except (FFmpegCommandError, VideoProbeError) as exc:
                failed += 1
                log.exception(f"{out_path.name} verification failed: {exc}")
                continue
            exported += 1
            log.verify(f"{out_path.name} OK duration={duration:.3f}s")

        return exported, failed

    def _cut_scene(self, ffmpeg: Path, out_path: Path, scene: Scene) -> None:
        """FFmpeg 精确切割：-ss/-to 放在 -i 之前 + 重新编码，保证分镜边界帧级准确。

        保持原始分辨率与 FPS（不缩放、不改帧率），CRF 18 视觉上接近无损；
        音频转 AAC 192k。Qwen3-VL 后续分析依赖边界准确性，因此不用 -c copy。
        """
        cmd = [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{scene.start:.3f}",
            "-to",
            f"{scene.end:.3f}",
            "-i",
            str(self.input_video),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired as exc:
            raise FFmpegCommandError(f"ffmpeg 切割超时: {out_path.name}") from exc
        if result.returncode != 0:
            raise FFmpegCommandError(
                f"ffmpeg 切割失败 (exit={result.returncode}): {out_path.name}\n{result.stderr.strip()[-500:]}"
            )

    def _write_scenes_json(self, info: VideoInfo, scenes: list[Scene], original_count: int, merged_count: int) -> Path:
        pp = self.postprocess
        payload = {
            "version": SCENES_JSON_VERSION,
            "episode": self.input_video.stem,
            "source": {
                "file": self.input_video.name,
                "duration": round(info.duration, 3),
                "fps": info.fps,
                "width": info.width,
                "height": info.height,
                "codec": info.codec,
            },
            "detector": {
                "type": self.config.type,
                "threshold": self.config.threshold,
                "min_scene_len_frames": self.config.min_scene_len_frames,
            },
            "postprocess": {
                "dry_run": pp.dry_run,
                "merge": {
                    "enabled": pp.merge_enabled,
                    "merge_threshold": pp.merge_threshold,
                    "cross_window": pp.cross_window,
                    "analysis_scale": pp.analysis_scale,
                    "merge_max_iterations": pp.merge_max_iterations,
                },
                "black": {
                    "enabled": pp.black_enabled,
                    "black_max_yavg": pp.black_max_yavg,
                    "black_max_ystd": pp.black_max_ystd,
                    "black_min_pblack": pp.black_min_pblack,
                    "black_pixel_threshold": pp.black_pixel_threshold,
                    "black_sample_frames": pp.black_sample_frames,
                    "black_min_frame_ratio": pp.black_min_frame_ratio,
                },
                "original_scene_count": original_count,
                "merged_boundary_count": merged_count,
                "black_scene_count": sum(1 for s in scenes if s.black),
            },
            "scenes": [scene.to_dict() for scene in scenes],
        }
        json_path = self.output_dir / SCENES_JSON_NAME
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.info(f"Wrote {SCENES_JSON_NAME} ({len(scenes)} scenes)")
        return json_path

    def _write_report(
        self,
        log: SplitLogger,
        frame_ranges: list[tuple[int, int, float, float]],
        scenes: list[Scene],
        reviews: list[dict],
        iterations: list[list[int]],
        black_report: list[dict],
    ) -> None:
        """后处理对比报告：逐边界复核分数与决策、合并迭代、黑场判定明细。"""
        pp = self.postprocess
        if not pp.needs_analysis():
            return
        merged_frames = {b for it in iterations for b in it}
        report = {
            "dry_run": pp.dry_run,
            "original_scene_count": len(frame_ranges),
            "final_scene_count": len(scenes),
            "config": {
                "merge_threshold": pp.merge_threshold,
                "cross_window": pp.cross_window,
                "analysis_scale": pp.analysis_scale,
                "merge_max_iterations": pp.merge_max_iterations,
                "black_max_yavg": pp.black_max_yavg,
                "black_max_ystd": pp.black_max_ystd,
                "black_min_pblack": pp.black_min_pblack,
                "black_pixel_threshold": pp.black_pixel_threshold,
                "black_sample_frames": pp.black_sample_frames,
                "black_min_frame_ratio": pp.black_min_frame_ratio,
            },
            "merge_iterations": [{"round": i + 1, "merged_frames": frames} for i, frames in enumerate(iterations)],
            "boundaries": reviews,
            "black_scenes": [b for b in black_report if b["black"]],
            "summary": {
                "boundaries_checked": len(reviews),
                "boundaries_merged": len(merged_frames),
                "black_scenes_marked": sum(1 for b in black_report if b["black"]),
            },
        }
        report_path = self.output_dir / REPORT_NAME
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(
            f"Wrote {REPORT_NAME}: 边界复核 {len(reviews)} 个，合并 {len(merged_frames)} 个，"
            f"黑场 {report['summary']['black_scenes_marked']} 个"
        )
