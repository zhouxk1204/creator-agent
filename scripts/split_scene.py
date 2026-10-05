"""Scene splitter: detect hard cuts with PySceneDetect and export per-shot MP4s.

Takes one video file (e.g. an episode segment produced by split_episode.py),
runs PySceneDetect's ContentDetector over it, and writes:

  output/<stem>_scenes/
    001.mp4 002.mp4 ...   one frame-accurate re-encoded MP4 per scene
    contact_sheet.jpg     labelled grid of one thumbnail per scene (for review)
    scenes.json           machine-readable scene list (times, frames, files)

Usage:
    uv run python scripts/split_scene.py input/935#1.mp4 --preview   # detect only, no MP4 export
    uv run python scripts/split_scene.py input/935#1.mp4             # detect + export MP4s
    uv run python scripts/split_scene.py input/935#1.mp4 --threshold 27 --min-scene-len 15

Notes:
  * --min-scene-len is in FRAMES (PySceneDetect's unit). At 24fps, 15 frames
    is ~0.6s. If you think in seconds: frames = seconds * fps.
  * MP4 export re-encodes (libx264) instead of stream-copying, because stream
    copy snaps cuts to keyframes and scene boundaries would be wrong.
  * Always eyeball contact_sheet.jpg from a --preview run before trusting a
    new threshold on a new source.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import cv2

# --- defaults tuned for film/anime hard cuts --------------------------------
DEFAULT_THRESHOLD = 27.0  # ContentDetector sensitivity (higher = fewer cuts)
DEFAULT_MIN_SCENE_LEN = 15  # frames; ~0.6s at 24fps
THUMB_W, THUMB_H = 320, 180  # contact sheet thumbnail size
THUMB_COLS_MAX = 5  # grid never wider than this
BLACK_MEAN = 12.0  # mean brightness below this = black frame, try another spot


def fmt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def fmt_time_short(t: float) -> str:
    m, s = divmod(int(round(t)), 60)
    return f"{m:02d}:{s:02d}"


def die(msg: str) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    raise SystemExit(1)


# --- detection ---------------------------------------------------------------


def detect_scenes(video_path: Path, threshold: float, min_scene_len: int):
    """Run ContentDetector; return (scenes, fps, duration_sec, total_frames).

    scenes is a list of (start_sec, end_sec, start_frame, end_frame) where
    end is exclusive (first frame of the next scene).
    """
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        die('未安装 PySceneDetect。运行: uv add "scenedetect[opencv-headless]>=0.6,<0.7"')

    try:
        video = open_video(str(video_path))
    except Exception as e:
        die(f"无法读取视频 {video_path}: {e}\n确认文件是有效的视频格式(mp4/mkv 等),且未损坏。")

    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len))
    try:
        manager.detect_scenes(video, show_progress=True)
    except Exception as e:
        die(f"PySceneDetect 检测失败: {e}\n可尝试降低 --threshold 或换用更短的片段先验证。")

    fps = video.frame_rate
    total_frames = video.duration.get_frames()
    duration = video.duration.get_seconds()

    scene_list = manager.get_scene_list()
    if not scene_list:
        # No cut found at all: treat the whole video as one scene.
        scene_list = [(video.base_timecode, video.duration)]

    scenes = []
    for start_tc, end_tc in scene_list:
        scenes.append(
            (
                start_tc.get_seconds(),
                end_tc.get_seconds(),
                start_tc.get_frames(),
                end_tc.get_frames(),
            )
        )
    return scenes, fps, duration, total_frames


# --- contact sheet -----------------------------------------------------------


def grab_thumbnail(cap: cv2.VideoCapture, scene, total_frames: int):
    """Pick a representative frame: scene middle, skipping black frames.

    Tries 50% / 25% / 75% / 10% / 90% of the scene; returns the first frame
    that isn't near-black, or the middle one as a last resort. Never raises.
    """
    start_f, end_f = scene[2], scene[3]
    length = max(end_f - start_f, 1)
    fallback = None
    for frac in (0.5, 0.25, 0.75, 0.1, 0.9):
        frame_no = min(start_f + int(length * frac), total_frames - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        if fallback is None:
            fallback = frame
        if frame.mean() >= BLACK_MEAN:
            return frame
    return fallback


def make_contact_sheet(video_path: Path, scenes, total_frames: int, out_path: Path) -> bool:
    """Write a labelled thumbnail grid. Returns True on success (never raises)."""
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            print("警告: 无法用 OpenCV 打开视频,跳过 contact sheet", file=sys.stderr)
            return False

        n = len(scenes)
        cols = min(THUMB_COLS_MAX, max(1, math.ceil(math.sqrt(n))))
        rows = math.ceil(n / cols)
        label_h = 34
        cell_h = THUMB_H + label_h
        sheet = None

        for i, scene in enumerate(scenes):
            thumb_img = grab_thumbnail(cap, scene, total_frames)
            cell = _labelled_cell(thumb_img, i + 1, scene[0], scene[1])
            r, c = divmod(i, cols)
            if sheet is None:
                import numpy as np

                sheet = np.full((rows * cell_h, cols * THUMB_W, 3), 24, dtype=np.uint8)
            sheet[r * cell_h : (r + 1) * cell_h, c * THUMB_W : (c + 1) * THUMB_W] = cell

        cap.release()
        if sheet is None:
            return False
        cv2.imwrite(str(out_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return True
    except Exception as e:
        print(f"警告: contact sheet 生成失败: {e}", file=sys.stderr)
        return False


def _labelled_cell(frame, index: int, start: float, end: float):
    import numpy as np

    cell = np.full((THUMB_H + 34, THUMB_W, 3), 32, dtype=np.uint8)
    if frame is not None:
        cell[34 : 34 + THUMB_H] = cv2.resize(frame, (THUMB_W, THUMB_H))
    else:
        cv2.putText(cell, "frame unreadable", (40, 34 + THUMB_H // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 90, 90), 1)
    cv2.putText(cell, f"V{index:03d}", (8, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(
        cell,
        f"{fmt_time_short(start)}-{fmt_time_short(end)}",
        (8, 31),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (170, 170, 170),
        1,
    )
    return cell


# --- export ------------------------------------------------------------------


def export_scene(video_path: Path, start: float, end: float, out_path: Path) -> None:
    """Cut [start, end) with ffmpeg, re-encoding for frame accuracy."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{end - start:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",  # audio optional: some sources have none
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "ffmpeg 未知错误")


# --- main --------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="用 PySceneDetect 检测镜头切换并拆分为独立 MP4。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("video", type=Path, help="输入视频文件, 例如 input/935#1.mp4")
    p.add_argument("-o", "--output", type=Path, default=Path("output"), help="输出根目录")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="ContentDetector 阈值 (越大越不敏感)")
    p.add_argument(
        "--min-scene-len",
        type=int,
        default=DEFAULT_MIN_SCENE_LEN,
        help="最短镜头长度, 单位是帧 (frames = 秒 × fps; 24fps 时 15 帧 ≈ 0.6s)",
    )
    p.add_argument("--preview", action="store_true", help="只检测 + 生成 contact_sheet/scenes.json, 不导出 MP4")
    args = p.parse_args()

    video: Path = args.video
    if not video.exists():
        die(f"输入文件不存在: {video}\n确认路径正确(文件名含 # 时建议加引号)。")
    if not video.is_file():
        die(f"输入不是文件: {video}")
    if not args.preview and shutil.which("ffmpeg") is None:
        die("找不到 ffmpeg,无法导出 MP4。请先安装: brew install ffmpeg")

    out_dir = args.output / f"{video.stem}_scenes"
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- detect ---
    scenes, fps, duration, total_frames = detect_scenes(video, args.threshold, args.min_scene_len)

    print(f"\n输入视频: {video}")
    print(f"视频时长: {fmt_time(duration)}")
    print(f"FPS: {fps:.3f}")
    print("检测器: ContentDetector")
    print(f"Threshold: {args.threshold}")
    print(f"Min scene length: {args.min_scene_len} frames ({args.min_scene_len / fps:.2f}s)\n")
    print(f"检测到 {len(scenes)} 个镜头\n")

    digits = max(3, len(str(len(scenes))))
    entries = []
    for i, (start, end, start_f, end_f) in enumerate(scenes, 1):
        name = f"{i:0{digits}d}.mp4"
        entries.append(
            {
                "id": f"V{i:03d}",
                "index": i,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "start_frame": start_f,
                "end_frame": end_f,
                "fps": round(fps, 3),
                "file": name,
            }
        )
        print(f"[{i:0{digits}d}/{len(scenes)}] {fmt_time(start)} → {fmt_time(end)}")

    # --- scenes.json ---
    json_path = out_dir / "scenes.json"
    doc = {
        "source": video.name,
        "detector": {"name": "ContentDetector", "threshold": args.threshold, "min_scene_len": args.min_scene_len},
        "fps": round(fps, 3),
        "duration": round(duration, 3),
        "scenes": entries,
    }
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- contact sheet ---
    sheet_path = out_dir / "contact_sheet.jpg"
    sheet_ok = make_contact_sheet(video, scenes, total_frames, sheet_path)

    # --- export MP4s ---
    failed: list[tuple[str, str]] = []
    if args.preview:
        print("\n[preview 模式] 跳过 MP4 导出")
    else:
        print()
        for entry, (start, end, _, _) in zip(entries, scenes):
            out_file = out_dir / entry["file"]
            try:
                export_scene(video, start, end, out_file)
            except Exception as e:
                failed.append((entry["file"], str(e)))
                print(f"  !! {entry['file']} 导出失败: {e}", file=sys.stderr)

    print("\n完成。")
    print(f"\n输出目录:\n{out_dir}/\n")
    print(f"分镜数量: {len(scenes)}")
    if sheet_ok:
        print(f"预览图:\n{sheet_path}")
    print(f"\nJSON:\n{json_path}")
    if failed:
        print(f"\n警告: {len(failed)} 个镜头导出失败(其余正常),可删除失败文件后重跑:", file=sys.stderr)
        for name, err in failed:
            print(f"  - {name}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
