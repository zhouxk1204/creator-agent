"""Extract 16kHz mono WAV audio from a downloaded video, for ASR."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def find_ffmpeg(ffmpeg_path: str = "") -> str:
    """Resolve the ffmpeg binary: explicit path, else PATH lookup."""
    if ffmpeg_path:
        return ffmpeg_path
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg not found on PATH; install it or set asr.ffmpeg_path")
    return found


def extract_audio(video_path: Path, out_path: Path, ffmpeg_path: str = "") -> Path:
    """Extract 16kHz mono PCM WAV from ``video_path`` into ``out_path``.

    FunASR's paraformer expects 16kHz mono. Idempotent: returns immediately if
    ``out_path`` already exists (re-runs / retries skip re-extraction).
    """
    video_path = Path(video_path)
    out_path = Path(out_path)
    if out_path.exists():
        logger.info("Audio already extracted, reusing: %s", out_path)
        return out_path
    if not video_path.exists():
        raise RuntimeError(f"Video file not found: {video_path}")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",  # drop video stream
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",  # 16 kHz
        "-ac",
        "1",  # mono
        str(out_path),
    ]
    logger.info("Extracting audio: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or "")[-500:]
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {tail}")
    if not out_path.exists():
        raise RuntimeError(f"ffmpeg produced no output file: {out_path}")
    return out_path
