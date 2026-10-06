"""核心流程：PySceneDetect 分镜检测 → FFmpeg 切割 → scenes.json。

未来阶段（SceneAnalyzer / SubtitleMatcher / ScriptGenerator）以 scenes.json
作为输入，本模块只做“分镜数据基础设施”。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from pathlib import Path

from models import DetectorConfig, Scene, VideoInfo
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
SCENES_JSON_VERSION = "1.0"


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

    def verify(self, message: str) -> None:
        self._emit("VERIFY", message)

    def error(self, message: str) -> None:
        self._emit("ERROR", message)

    def exception(self, message: str) -> None:
        """控制台打印简明错误，日志文件追加完整 traceback。"""
        self.error(message)
        self._file_only.error(traceback.format_exc())


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
    ) -> None:
        self.input_video = Path(input_video)
        self.output_dir = Path(output_dir) if output_dir is not None else self.input_video.with_suffix("")
        self.config = config or DetectorConfig()
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
            return self._run_pipeline(ffmpeg, ffprobe, log)
        except SceneSplitError as exc:
            log.exception(str(exc))
            raise
        except Exception as exc:  # 未预期错误：同样转成明确信息 + 文件里留 traceback
            log.exception(f"未预期的错误: {exc}")
            raise SceneSplitError(f"未预期的错误: {exc}") from exc

    # ------------------------------------------------------------- pipeline

    def _run_pipeline(self, ffmpeg: Path, ffprobe: Path, log: SplitLogger) -> SplitSummary:
        info = probe_video(self.input_video, ffprobe)
        self._log_header(log, info, ffmpeg)

        log.info("Detecting scenes...")
        raw_scenes = detect_scenes(self.input_video, self.config)
        scenes = self._build_scenes(raw_scenes)
        for scene in scenes:
            log.scene(scene)
        log.info(f"Detected scenes: {len(scenes)}")

        exported = failed = 0
        if self.export:
            log.info("Starting video extraction...")
            exported, failed = self._export_scenes(scenes, ffmpeg, ffprobe, log)

        scenes_json = self._write_scenes_json(info, scenes)

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
            for stale in (out / SCENES_JSON_NAME, out / LOG_NAME):
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

    def _build_scenes(self, raw_scenes) -> list[Scene]:
        scenes: list[Scene] = []
        for index, (start, end) in enumerate(raw_scenes, start=1):
            start_sec = start.get_seconds()
            end_sec = end.get_seconds()
            scenes.append(
                Scene(
                    scene_id=f"{index:04d}",
                    start=start_sec,
                    end=end_sec,
                    duration=end_sec - start_sec,
                    start_frame=start.get_frames(),
                    end_frame=end.get_frames(),
                    start_timecode=format_timecode(start_sec),
                    end_timecode=format_timecode(end_sec),
                    video=(f"{SCENES_DIR_NAME}/{index:04d}.mp4" if self.export else ""),
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

    def _write_scenes_json(self, info: VideoInfo, scenes: list[Scene]) -> Path:
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
            "scenes": [scene.to_dict() for scene in scenes],
        }
        json_path = self.output_dir / SCENES_JSON_NAME
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.info(f"Wrote {SCENES_JSON_NAME} ({len(scenes)} scenes)")
        return json_path
