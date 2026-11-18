"""Upscale explicit story videos into their materials directory.

The third-party Real-ESRGAN engine and model remain in their existing locations.
Each input is processed sequentially in its own disposable workspace. A completed
video is first encoded as ``<name>_4K.part.mp4`` and atomically promoted to
``<name>_4K.mp4``; frame directories and partial output are removed after every
video, including failures.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REALESRGAN_DIR = Path(r"C:\to4k\Real-ESRGAN")
PYTHON_EXE = Path(sys.executable)
FFMPEG_EXE = REALESRGAN_DIR / "ffmpeg.exe"
INFERENCE_SCRIPT = REALESRGAN_DIR / "inference_realesrgan.py"
MODEL_PATH = REALESRGAN_DIR / "weights/2x_AniScale2S_Compact_i8_60K.pth"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi"}


def log(message: str, log_file: Path) -> None:
    print(message, flush=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def run(command: list[object], log_file: Path, cwd: Path | None = None) -> None:
    log(f"[CMD] {subprocess.list2cmdline([str(part) for part in command])}", log_file)
    subprocess.run(command, cwd=cwd, check=True)


def check_setup() -> None:
    required = (
        ("upscale Python", PYTHON_EXE),
        ("Real-ESRGAN inference script", INFERENCE_SCRIPT),
        ("Real-ESRGAN model", MODEL_PATH),
        ("ffmpeg", FFMPEG_EXE),
    )
    missing = [f"{label}: {path}" for label, path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Missing upscale dependency:\n" + "\n".join(missing))


def _clean_workspace(workspace: Path, work_root: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    try:
        work_root.rmdir()
    except OSError:
        pass


def upscale(
    video: Path,
    output_dir: Path,
    work_root: Path,
    log_file: Path,
    *,
    force: bool = False,
) -> bool:
    """Upscale one video and remove all of its temporary artifacts afterward."""
    output = output_dir / f"{video.stem}_4K.mp4"
    temp_output = output.with_name(f"{output.stem}.part.mp4")
    workspace = work_root / video.stem
    in_frames = workspace / "in_frames"
    out_frames = workspace / "out_frames"
    log(f"\n=== Processing: {video.name} ===", log_file)

    if output.is_file() and output.stat().st_size > 0 and not force:
        _clean_workspace(workspace, work_root)
        temp_output.unlink(missing_ok=True)
        log(f"[SKIP] Output already exists: {output}", log_file)
        return False

    _clean_workspace(workspace, work_root)
    try:
        in_frames.mkdir(parents=True)
        out_frames.mkdir(parents=True)
        run(
            [
                FFMPEG_EXE,
                "-y",
                "-hwaccel",
                "cuda",
                "-i",
                video,
                "-q:v",
                "2",
                in_frames / "frame_%06d.jpg",
            ],
            log_file,
        )
        run(
            [
                PYTHON_EXE,
                INFERENCE_SCRIPT,
                "-n",
                "realesr-animevideov3",
                "--model_path",
                MODEL_PATH,
                "-i",
                in_frames,
                "-o",
                out_frames,
                "--outscale",
                "2",
                "--tile",
                "0",
                "-g",
                "0",
            ],
            log_file,
            cwd=REALESRGAN_DIR,
        )
        run(
            [
                FFMPEG_EXE,
                "-y",
                "-r",
                "30",
                "-i",
                out_frames / "frame_%06d_out.jpg",
                "-i",
                video,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "h264_nvenc",
                "-preset",
                "slow",
                "-rc:v",
                "vbr_hq",
                "-cq:v",
                "19",
                "-b:v",
                "0",
                "-pix_fmt",
                "yuv420p",
                temp_output,
            ],
            log_file,
        )
        os.replace(temp_output, output)
        log(f"[SUCCESS] {output}", log_file)
        return True
    finally:
        _clean_workspace(workspace, work_root)
        temp_output.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Upscale explicit videos into one output directory")
    parser.add_argument("videos", nargs="+", type=Path, help="video files to upscale, in processing order")
    parser.add_argument("--output-dir", type=Path, required=True, help="directory for *_4K.mp4 and run_log.txt")
    parser.add_argument("--work-dir", type=Path, help="temporary workspace (default: OUTPUT/.upscale_tmp)")
    parser.add_argument("--force", action="store_true", help="replace existing *_4K.mp4 files")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = (args.work_dir or output_dir / ".upscale_tmp").expanduser().resolve()
    log_file = output_dir / "run_log.txt"
    log_file.write_text("===== Real-ESRGAN Python Tool =====\n", encoding="utf-8")

    videos = [video.expanduser().resolve() for video in args.videos]
    invalid = [video for video in videos if not video.is_file() or video.suffix.lower() not in VIDEO_EXTS]
    if invalid:
        for video in invalid:
            log(f"[ERROR] Input video missing or unsupported: {video}", log_file)
        return 1

    try:
        check_setup()
    except RuntimeError as exc:
        log(f"[ERROR] {exc}", log_file)
        return 1

    log(f"[INFO] Processing {len(videos)} explicit video(s)" + (" (--force)" if args.force else ""), log_file)
    failed: list[Path] = []
    done = 0
    for video in videos:
        try:
            if upscale(video, output_dir, work_root, log_file, force=args.force):
                done += 1
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            failed.append(video)
            log(f"[ERROR] {video.name}: {exc}", log_file)

    if failed:
        log(f"\n===== FAILED: {len(failed)} of {len(videos)} videos =====", log_file)
        return 1

    log(f"\n===== ALL DONE ({done} upscaled, {len(videos) - done} already existed) =====", log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
