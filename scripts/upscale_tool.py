"""Upscale every video in C:\\test\\temps with the local Real-ESRGAN setup.

The third-party engine, model, and dedicated conda environment stay in their
existing locations; this script only owns the extract/upscale/rebuild workflow.
It is called by bat/doraemon/run_upscale.bat and returns a non-zero exit code if
any video fails, so callers can use it without an interactive pause.

Reruns only do the missing work: a video whose ``<name>_4K.mp4`` already exists
is skipped (``--force`` redoes it). The rebuild writes ``<name>_4K.part.mp4``
first and renames it only after ffmpeg succeeds, so an interrupted run never
leaves a truncated file that a later run would mistake for finished.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(r"C:\test")
INPUT_DIR = BASE_DIR / "temps"
IN_FRAMES = BASE_DIR / "in_frames"
OUT_FRAMES = BASE_DIR / "out_frames"

REALESRGAN_DIR = Path(r"C:\to4k\Real-ESRGAN")
PYTHON_EXE = Path(r"C:\Users\34696\miniconda3\envs\upscale\python.exe")
FFMPEG_EXE = REALESRGAN_DIR / "ffmpeg.exe"
INFERENCE_SCRIPT = REALESRGAN_DIR / "inference_realesrgan.py"
MODEL_PATH = REALESRGAN_DIR / "weights/2x_AniScale2S_Compact_i8_60K.pth"
LOG_FILE = BASE_DIR / "run_log.txt"

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi"}
FORCE = "--force" in sys.argv[1:]


def log(msg):
    print(msg, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run(cmd, cwd=None):
    log(f"[CMD] {subprocess.list2cmdline([str(part) for part in cmd])}")
    subprocess.run(cmd, cwd=cwd, check=True)


def clean_dirs():
    for directory in (IN_FRAMES, OUT_FRAMES):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)


def get_videos():
    return sorted(path for path in INPUT_DIR.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTS)


def check_setup():
    required = (
        ("upscale Python", PYTHON_EXE),
        ("Real-ESRGAN inference script", INFERENCE_SCRIPT),
        ("Real-ESRGAN model", MODEL_PATH),
        ("ffmpeg", FFMPEG_EXE),
    )
    missing = [f"{label}: {path}" for label, path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Missing upscale dependency:\n" + "\n".join(missing))


def upscale(video):
    name = video.stem
    output = BASE_DIR / f"{name}_4K.mp4"
    # Encode to a .part name and rename only on success: a killed run otherwise
    # leaves a truncated mp4 that the skip check above would accept as done.
    tmp_output = output.with_name(f"{output.stem}.part.mp4")
    log(f"\n=== Processing: {name} ===")

    if output.exists() and not FORCE:
        log(f"[SKIP] Output already exists: {output}")
        return False

    try:
        clean_dirs()
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
                IN_FRAMES / "frame_%06d.jpg",
            ]
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
                IN_FRAMES,
                "-o",
                OUT_FRAMES,
                "--outscale",
                "2",
                "--tile",
                "0",
                "-g",
                "0",
            ],
            cwd=REALESRGAN_DIR,
        )
        run(
            [
                FFMPEG_EXE,
                "-y",
                "-r",
                "30",
                "-i",
                OUT_FRAMES / "frame_%06d_out.jpg",
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
                tmp_output,
            ]
        )
        os.replace(tmp_output, output)
        log(f"[SUCCESS] {output}")
    finally:
        clean_dirs()
        if tmp_output.exists():  # aborted encode: never promote half a file
            tmp_output.unlink(missing_ok=True)
    return True


def main():
    LOG_FILE.write_text("===== Real-ESRGAN Python Tool =====\n", encoding="utf-8")

    if not INPUT_DIR.is_dir():
        log(f"[ERROR] Input directory does not exist: {INPUT_DIR}")
        return 1

    videos = get_videos()
    if not videos:
        log(f"[ERROR] No videos found in: {INPUT_DIR}")
        return 1

    try:
        check_setup()
    except RuntimeError as exc:
        log(f"[ERROR] {exc}")
        return 1

    log(f"[INFO] Found {len(videos)} videos" + (" (--force: redo finished ones)" if FORCE else ""))
    failed = []
    done = 0
    for video in videos:
        try:
            if upscale(video):
                done += 1
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            failed.append(video)
            log(f"[ERROR] {video.name}: {exc}")

    if failed:
        log(f"\n===== FAILED: {len(failed)} of {len(videos)} videos =====")
        return 1

    log(f"\n===== ALL DONE ({done} upscaled, {len(videos) - done} already existed) =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
