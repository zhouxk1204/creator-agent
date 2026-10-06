"""FFmpeg / ffprobe 查找、视频信息探测、时间码格式化、导出结果校验。"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

from models import VideoInfo

#: Windows 上 FFmpeg 的默认安装位置（不假设已加入 PATH）。
WINDOWS_FFMPEG_DIR = Path(r"C:\ai\ffmpeg\bin")


class FFmpegNotFoundError(RuntimeError):
    """找不到 ffmpeg / ffprobe。"""


class VideoProbeError(RuntimeError):
    """ffprobe 读取视频信息失败。"""


class FFmpegCommandError(RuntimeError):
    """ffmpeg 命令执行失败。"""


def _platform_fallbacks(exe_name: str) -> list[Path]:
    """PATH 之外的候选路径，按平台返回。"""
    system = platform.system()
    if system == "Windows":
        return [WINDOWS_FFMPEG_DIR / f"{exe_name}.exe"]
    if system == "Darwin":
        return [
            Path(f"/opt/homebrew/bin/{exe_name}"),
            Path(f"/usr/local/bin/{exe_name}"),
        ]
    return [
        Path(f"/usr/bin/{exe_name}"),
        Path(f"/usr/local/bin/{exe_name}"),
    ]


def find_tool(exe_name: str, env_var: str) -> Path:
    """按顺序查找可执行文件：环境变量 → PATH → 平台默认安装目录。

    找到则返回路径并确认 ``-version`` 可执行；找不到抛出 FFmpegNotFoundError，
    错误信息中列出所有检查过的位置。
    """
    checked: list[str] = []

    env_value = os.environ.get(env_var)
    if env_value:
        candidate = Path(env_value)
        checked.append(f"{env_var}={candidate}")
        if candidate.is_file():
            return candidate

    which_result = shutil.which(exe_name)
    if which_result:
        return Path(which_result)
    checked.append(f"PATH (未找到 {exe_name})")

    for candidate in _platform_fallbacks(exe_name):
        checked.append(str(candidate))
        if candidate.is_file():
            return candidate

    locations = "\n  ".join(checked) if checked else "(无)"
    raise FFmpegNotFoundError(
        f"{exe_name} not found. 已检查:\n  {locations}\n"
        f"请安装 FFmpeg，或设置环境变量 {env_var} 指向 {exe_name} 的完整路径。"
    )


def find_ffmpeg() -> Path:
    return find_tool("ffmpeg", "FFMPEG_PATH")


def find_ffprobe() -> Path:
    return find_tool("ffprobe", "FFPROBE_PATH")


def parse_frame_rate(rate_str: str) -> float:
    """解析 ffprobe 的 r_frame_rate，如 "24000/1001" → 23.976（不向上取整成 24）。"""
    if not rate_str or rate_str == "0/0":
        return 0.0
    if "/" in rate_str:
        num, _, den = rate_str.partition("/")
        try:
            denominator = float(den)
            if denominator == 0:
                return 0.0
            return round(float(num) / denominator, 3)
        except ValueError:
            return 0.0
    try:
        return round(float(rate_str), 3)
    except ValueError:
        return 0.0


def _run_json(cmd: list[str]) -> dict:
    """执行命令并把 stdout 解析为 JSON，失败时抛出带 stderr 的明确异常。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise FFmpegCommandError(f"命令超时: {cmd[0]}") from exc
    if result.returncode != 0:
        raise FFmpegCommandError(f"命令失败 (exit={result.returncode}): {cmd[0]}\n{result.stderr.strip()[-500:]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VideoProbeError(f"无法解析 {cmd[0]} 输出: {result.stdout[:200]}") from exc


def probe_video(video_path: Path, ffprobe: Path) -> VideoInfo:
    """用 ffprobe 读取分辨率 / fps / 帧数 / 时长 / 编码。"""
    data = _run_json(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_frames,codec_name:format=duration",
            "-of",
            "json",
            str(video_path),
        ]
    )
    streams = data.get("streams") or []
    if not streams:
        raise VideoProbeError(f"视频中没有视频流: {video_path}")
    stream = streams[0]

    fps = parse_frame_rate(stream.get("r_frame_rate", "0/0"))
    duration = float(data.get("format", {}).get("duration", 0) or 0)

    nb_frames = stream.get("nb_frames")
    if nb_frames and str(nb_frames).isdigit():
        frame_count = int(nb_frames)
    elif fps > 0 and duration > 0:
        frame_count = round(fps * duration)  # nb_frames 缺失时按时长估算
    else:
        frame_count = 0

    return VideoInfo(
        path=video_path,
        width=int(stream.get("width", 0)),
        height=int(stream.get("height", 0)),
        fps=fps,
        frame_count=frame_count,
        duration=duration,
        codec=stream.get("codec_name", "unknown"),
    )


def format_timecode(seconds: float) -> str:
    """秒 → "HH:MM:SS.mmm"（毫秒精度）。"""
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def verify_video_file(path: Path, ffprobe: Path) -> float:
    """校验导出的分镜视频：存在、非空、ffprobe 可读、duration > 0。返回时长（秒）。"""
    if not path.is_file():
        raise VideoProbeError(f"文件不存在: {path}")
    if path.stat().st_size == 0:
        raise VideoProbeError(f"文件大小为 0: {path}")
    data = _run_json(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    if duration <= 0:
        raise VideoProbeError(f"duration <= 0: {path}")
    return duration
