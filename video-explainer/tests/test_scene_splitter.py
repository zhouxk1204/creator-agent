"""SceneSplitter 集成测试 + 工具函数单元测试。

测试视频由 ffmpeg lavfi 现场生成：纯色块硬切对 ContentDetector 来说是
非常明显的场景切换。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import requires_ffmpeg
from models import DetectorConfig
from scene_splitter import SceneSplitError, SceneSplitter
from video_utils import (
    FFmpegNotFoundError,
    find_ffprobe,
    find_tool,
    format_timecode,
    parse_frame_rate,
    verify_video_file,
)


def make_video(path: Path, colors: list[str], segment_seconds: float = 3.0, fps: int = 24) -> None:
    """用 ffmpeg 把若干纯色段拼成一个视频（每段之间是硬切）。"""
    inputs: list[str] = []
    for color in colors:
        inputs += [
            "-f",
            "lavfi",
            "-i",
            f"color={color}:s=320x240:r={fps}:d={segment_seconds}",
        ]
    concat_in = "".join(f"[{i}:v]" for i in range(len(colors)))
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *inputs,
        "-filter_complex",
        f"{concat_in}concat=n={len(colors)}:v=1[out]",
        "-map",
        "[out]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------- 单元测试


def test_parse_frame_rate_keeps_fractional_fps():
    assert parse_frame_rate("24000/1001") == pytest.approx(23.976, abs=1e-3)
    assert parse_frame_rate("30000/1001") == pytest.approx(29.97, abs=1e-3)
    assert parse_frame_rate("25/1") == 25.0
    assert parse_frame_rate("30/1") == 30.0
    assert parse_frame_rate("0/0") == 0.0


def test_format_timecode():
    assert format_timecode(0) == "00:00:00.000"
    assert format_timecode(4.208) == "00:00:04.208"
    assert format_timecode(643.7) == "00:10:43.700"


def test_find_tool_reports_checked_locations(monkeypatch):
    """Test 5: FFmpeg 不存在时必须得到明确错误，列出检查过的位置。"""
    monkeypatch.setattr("video_utils.shutil.which", lambda name: None)
    monkeypatch.setattr("video_utils._platform_fallbacks", lambda name: [])
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    with pytest.raises(FFmpegNotFoundError, match="not found"):
        find_tool("ffmpeg", "FFMPEG_PATH")


def test_find_tool_uses_env_var(tmp_path, monkeypatch):
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("FFMPEG_PATH", str(fake))
    assert find_tool("ffmpeg", "FFMPEG_PATH") == fake


# ---------------------------------------------------------------- 集成测试


@requires_ffmpeg
def test_normal_video_split(tmp_path):
    """Test 1: 正常视频 → scenes.json + scenes/*.mp4。"""
    video = tmp_path / "normal.mp4"
    make_video(video, ["red", "blue", "green"])
    out = tmp_path / "normal"

    summary = SceneSplitter(video, out).run()

    assert summary.ok
    assert len(summary.scenes) >= 3
    json_path = out / "scenes.json"
    assert json_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["version"] == "1.0"
    assert data["episode"] == "normal"
    assert data["source"]["fps"] == 24.0
    assert data["source"]["width"] == 320
    assert data["detector"]["threshold"] == 27.0

    first = data["scenes"][0]
    assert first["scene_id"] == "0001"
    assert first["start"] == 0.0
    assert first["end"] > 0
    assert first["start_frame"] == 0
    assert first["video"] == "scenes/0001.mp4"  # 相对路径
    assert ":" not in first["video"]

    scene_files = sorted((out / "scenes").glob("*.mp4"))
    assert len(scene_files) == len(data["scenes"])
    assert (out / "scene_split.log").is_file()

    ffprobe = find_ffprobe()
    for scene_file in scene_files:
        assert verify_video_file(scene_file, ffprobe) > 0


@requires_ffmpeg
def test_video_without_cuts_yields_single_scene(tmp_path):
    """Test 2: 没有明显切换的视频 → 至少得到 0001.mp4，而不是报错。"""
    video = tmp_path / "static.mp4"
    make_video(video, ["black"], segment_seconds=5.0)
    out = tmp_path / "static"

    summary = SceneSplitter(video, out).run()

    assert summary.ok
    assert len(summary.scenes) == 1
    assert (out / "scenes" / "0001.mp4").is_file()


@requires_ffmpeg
def test_very_short_video(tmp_path):
    """Test 3: 2 秒短视频正常处理。"""
    video = tmp_path / "short.mp4"
    make_video(video, ["white"], segment_seconds=2.0)
    out = tmp_path / "short"

    summary = SceneSplitter(video, out).run()

    assert summary.ok
    assert len(summary.scenes) == 1
    assert summary.scenes[0].duration == pytest.approx(2.0, abs=0.2)


@requires_ffmpeg
def test_existing_output_dir_refused_unless_overwrite(tmp_path):
    """Test 4: 输出目录已存在 → 默认拒绝；overwrite=True 才覆盖。"""
    video = tmp_path / "dup.mp4"
    make_video(video, ["red", "blue"], segment_seconds=2.0)
    out = tmp_path / "dup"

    SceneSplitter(video, out).run()

    with pytest.raises(SceneSplitError, match="已存在"):
        SceneSplitter(video, out).run()

    summary = SceneSplitter(video, out, overwrite=True).run()
    assert summary.ok


@requires_ffmpeg
def test_no_export_only_writes_json(tmp_path):
    """--no-export: 只检测分镜，不切视频。"""
    video = tmp_path / "detect_only.mp4"
    make_video(video, ["red", "blue", "green"], segment_seconds=2.0)
    out = tmp_path / "detect_only"

    summary = SceneSplitter(video, out, export=False).run()

    assert summary.ok
    assert (out / "scenes.json").is_file()
    assert not (out / "scenes").exists()


@requires_ffmpeg
def test_fractional_fps_preserved(tmp_path):
    """23.976fps 的视频不能被当成 24fps。"""
    video = tmp_path / "frac.mp4"
    # 用 -r 24000/1001 生成真正的 23.976fps 视频
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=red:s=320x240:r=24000/1001:d=4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    out = tmp_path / "frac"

    summary = SceneSplitter(video, out).run()

    data = json.loads((out / "scenes.json").read_text(encoding="utf-8"))
    assert data["source"]["fps"] == pytest.approx(23.976, abs=0.001)
    assert data["source"]["fps"] != 24.0
    assert summary.ok


@requires_ffmpeg
def test_min_scene_len_merges_flash_cuts(tmp_path):
    """min_scene_len 过短的闪切不应各自成为独立分镜。"""
    video = tmp_path / "flash.mp4"
    # 每段仅 0.25s（6 帧 @24fps）的连续闪切 + 一个长段
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=red:s=320x240:r=24:d=4",
        "-f",
        "lavfi",
        "-i",
        "color=white:s=320x240:r=24:d=0.25",
        "-f",
        "lavfi",
        "-i",
        "color=black:s=320x240:r=24:d=0.25",
        "-f",
        "lavfi",
        "-i",
        "color=blue:s=320x240:r=24:d=4",
        "-filter_complex",
        "[0:v][1:v][2:v][3:v]concat=n=4:v=1[out]",
        "-map",
        "[out]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    out = tmp_path / "flash"

    summary = SceneSplitter(video, out, config=DetectorConfig(min_scene_len_frames=12)).run()

    durations = [s.duration for s in summary.scenes]
    # 12 帧 @24fps = 0.5s，不应出现短于 0.5s 的分镜
    assert all(d >= 0.45 for d in durations), durations


def test_missing_input_file(tmp_path):
    with pytest.raises(SceneSplitError, match="不存在"):
        SceneSplitter(tmp_path / "nope.mp4").run()
