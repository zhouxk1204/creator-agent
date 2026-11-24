"""Upscale explicit story videos into their materials directory.

The third-party Real-ESRGAN engine and model remain in their existing locations.
Each input is processed sequentially in its own disposable workspace. A completed
video is first encoded as ``<name>_4K.part.mp4`` and atomically promoted to
``<name>_4K.mp4``; frame directories and partial output are removed after every
video, including failures.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from live_logging import LiveLog, install_interrupt_handler, progress_bar, run_logged

REALESRGAN_DIR = Path(r"C:\to4k\Real-ESRGAN")
PYTHON_EXE = Path(sys.executable)
FFMPEG_EXE = REALESRGAN_DIR / "ffmpeg.exe"
INFERENCE_SCRIPT = REALESRGAN_DIR / "inference_realesrgan.py"
MODEL_PATH = REALESRGAN_DIR / "weights/2x_AniScale2S_Compact_i8_60K.pth"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi"}


def log(message: str, log_file: Path) -> None:
    LiveLog(log_file, "upscale").event(message)


def run(
    command: list[object],
    log_file: Path,
    cwd: Path | None = None,
    *,
    progress: Callable[[], str] | None = None,
    output_filter: Callable[[str], bool] | None = None,
) -> None:
    args = [str(part) for part in command]
    rc = run_logged(
        args, LiveLog(log_file, "upscale"), cwd=cwd, progress=progress, quiet=True, output_filter=output_filter
    )
    if rc:
        raise subprocess.CalledProcessError(rc, args)


def _frames(directory: Path, pattern: str) -> int:
    # scandir reuses directory-entry metadata instead of stat-ing every JPG
    # at each refresh, which matters for videos with tens of thousands of frames.
    with os.scandir(directory) as entries:
        return sum(entry.is_file() and fnmatch.fnmatchcase(entry.name, pattern) for entry in entries)


def video_info(video: Path) -> dict:
    """Read header metadata without decoding the entire source video."""
    candidate = FFMPEG_EXE.with_name("ffprobe" + FFMPEG_EXE.suffix)
    probe = str(candidate) if candidate.is_file() else shutil.which("ffprobe")
    if not probe:
        return {}
    try:
        result = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,nb_frames,duration:format=duration",
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode:
            return {}
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        fps = None
        for key in ("avg_frame_rate", "r_frame_rate"):
            try:
                value = float(Fraction(stream.get(key, "0")))
                if value > 0:
                    fps = value
                    break
            except (ValueError, ZeroDivisionError, TypeError):
                pass
        duration = 0.0
        for value in (stream.get("duration"), data.get("format", {}).get("duration")):
            try:
                duration = float(value)
                if duration > 0:
                    break
            except (ValueError, TypeError):
                continue
        raw_frames = stream.get("nb_frames")
        frames = int(raw_frames) if raw_frames and str(raw_frames).isdigit() else None
        estimated = not frames and bool(fps and duration > 0)
        if estimated:
            frames = round(fps * duration)
        return {
            "width": stream.get("width"),
            "height": stream.get("height"),
            "codec": stream.get("codec_name"),
            "fps": fps,
            "duration": duration,
            "frames": frames,
            "estimated": estimated,
        }
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError, OverflowError):
        return {}


def _extraction_progress(directory: Path, total: int | None, frame_label: str) -> str:
    done = _frames(directory, "frame_*.jpg")
    return f"[EXTRACT] {progress_bar(done, total)} {done}/{frame_label} frames"


def _inference_output(line: str) -> bool:
    """Hide the engine's one-line-per-image chatter, but preserve diagnostics."""
    return re.fullmatch(r"\s*(?:Testing \d+ frame_\d+|Tile \d+/\d+)\s*", line) is None


class EncoderProgress:
    def __init__(self, total: int, output: Path):
        self.total = total
        self.output = output
        self.frames = 0

    def output_line(self, line: str) -> bool:
        key, separator, value = line.strip().partition("=")
        if separator and key == "frame":
            try:
                self.frames = max(self.frames, int(value))
            except ValueError:
                pass
            return False
        return not (
            separator
            and (
                key
                in {
                    "fps",
                    "bitrate",
                    "total_size",
                    "out_time_us",
                    "out_time_ms",
                    "out_time",
                    "dup_frames",
                    "drop_frames",
                    "speed",
                    "progress",
                }
                or re.fullmatch(r"stream_\d+_\d+_q", key)
            )
        )

    def status(self) -> str:
        size = self.output.stat().st_size / 1024 / 1024 if self.output.is_file() else 0
        return (
            f"[ENCODE] {progress_bar(self.frames, self.total)} {self.frames}/{self.total} frames; output={size:.1f} MiB"
        )


def _inference_progress(directory: Path, total: int, started: float) -> str:
    done = _frames(directory, "frame_*_out.jpg")
    elapsed = max(time.monotonic() - started, 0.001)
    rate = done / elapsed
    eta = f"{max(total - done, 0) / rate:.0f}s" if rate else "unknown"
    percent = done * 100 / total if total else 0
    return (
        f"[INFERENCE] {progress_bar(done, total)} {done}/{total} frames ({percent:.1f}%); "
        f"{rate:.2f} frames/s; ETA={eta}"
    )


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


def is_complete(video: Path, output_dir: Path) -> bool:
    """Only final, nonempty outputs count; *.part.mp4 is never a checkpoint."""
    output = output_dir / f"{video.stem}_4K.mp4"
    return output.is_file() and output.stat().st_size > 0


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
    log(f"\n=== Processing: {video.name}; source={video.stat().st_size / 1024 / 1024:.1f} MiB ===", log_file)
    log(f"[PATHS] output={output}; temporary workspace={workspace}", log_file)

    if is_complete(video, output_dir) and not force:
        _clean_workspace(workspace, work_root)
        temp_output.unlink(missing_ok=True)
        log(f"[SKIP] Output already exists: {output}", log_file)
        return False

    info = video_info(video)
    source_frames = info.get("frames")
    frame_label = ("~" if info.get("estimated") else "") + str(source_frames) if source_frames else "unknown"
    fps_label = f"{info['fps']:.3f}" if info.get("fps") else "unknown"
    duration_label = f"{info['duration']:.2f}s" if info.get("duration") else "unknown"
    log(
        f"[VIDEO INFO] resolution={info.get('width') or '?'}x{info.get('height') or '?'}; "
        f"codec={info.get('codec') or 'unknown'}; FPS={fps_label}; duration={duration_label}; frames={frame_label}",
        log_file,
    )
    _clean_workspace(workspace, work_root)
    try:
        temp_output.unlink(missing_ok=True)
        in_frames.mkdir(parents=True)
        out_frames.mkdir(parents=True)
        log("[STAGE 1/3] Extracting source frames with CUDA ffmpeg", log_file)
        run(
            [
                FFMPEG_EXE,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-nostats",
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
            progress=lambda: _extraction_progress(in_frames, source_frames, frame_label),
        )
        total = _frames(in_frames, "frame_*.jpg")
        if not total:
            raise RuntimeError("ffmpeg extracted no frames")
        log(f"[EXTRACT DONE] {total} frames; [STAGE 2/3] Real-ESRGAN 2x inference", log_file)
        inference_started = time.monotonic()
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
            progress=lambda: _inference_progress(out_frames, total, inference_started),
            output_filter=_inference_output,
        )
        completed = _frames(out_frames, "frame_*_out.jpg")
        if completed != total:
            raise RuntimeError(f"Incomplete upscaled frames: {completed}/{total}")
        log(f"[INFERENCE DONE] {completed}/{total} frames; [STAGE 3/3] Encoding video and source audio", log_file)
        encoder = EncoderProgress(total, temp_output)
        run(
            [
                FFMPEG_EXE,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-nostats",
                "-progress",
                "pipe:1",
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
            progress=encoder.status,
            output_filter=encoder.output_line,
        )
        if not temp_output.is_file() or temp_output.stat().st_size == 0:
            raise RuntimeError("Encoder produced no video; completed output was not replaced")
        os.replace(temp_output, output)
        log(f"[SUCCESS] {output}; size={output.stat().st_size / 1024 / 1024:.1f} MiB", log_file)
        return True
    finally:
        _clean_workspace(workspace, work_root)
        temp_output.unlink(missing_ok=True)
        log(f"[CLEANUP] Removed temporary frames and partial output for {video.name}; source video retained", log_file)


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
    log("===== Real-ESRGAN RUN START (append log, resume completed videos) =====", log_file)

    videos = [video.expanduser().resolve() for video in args.videos]
    invalid = [video for video in videos if not video.is_file() or video.suffix.lower() not in VIDEO_EXTS]
    if invalid:
        for video in invalid:
            log(f"[ERROR] Input video missing or unsupported: {video}", log_file)
        return 1

    if args.force or any(not is_complete(video, output_dir) for video in videos):
        try:
            check_setup()
        except RuntimeError as exc:
            log(f"[ERROR] {exc}", log_file)
            return 1

    log(f"[INFO] Processing {len(videos)} explicit video(s)" + (" (--force)" if args.force else ""), log_file)
    failed: list[Path] = []
    done = 0
    for index, video in enumerate(videos, 1):
        log(f"[VIDEO {index}/{len(videos)}] {video.name}", log_file)
        try:
            if upscale(video, output_dir, work_root, log_file, force=args.force):
                done += 1
        except KeyboardInterrupt:
            log(
                f"[INTERRUPTED] {video.name} is unfinished; rerun the same bat to skip completed videos and retry it",
                log_file,
            )
            return 130
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            failed.append(video)
            log(f"[ERROR] {video.name}: {exc}", log_file)

    if failed:
        log(f"\n===== FAILED: {len(failed)} of {len(videos)} videos =====", log_file)
        return 1

    log(f"\n===== ALL DONE ({done} upscaled, {len(videos) - done} already existed) =====", log_file)
    return 0


if __name__ == "__main__":
    install_interrupt_handler()
    sys.exit(main())
