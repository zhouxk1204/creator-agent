"""Extract 16kHz mono WAV audio from a downloaded video, for ASR."""

from __future__ import annotations

import json
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


def _sidecar_path(out_path: Path) -> Path:
    return out_path.with_suffix(out_path.suffix + ".src.json")


def _cache_hit(video_path: Path, out_path: Path) -> bool:
    """Reusing a cached WAV is only safe when it was extracted from THIS
    source file — a same-named replacement video must not silently reuse the
    old audio (the whole subtitle timeline would be for the wrong content).
    Validity = sidecar exists AND source size+mtime match."""
    if not out_path.exists():
        return False
    try:
        meta = json.loads(_sidecar_path(out_path).read_text(encoding="utf-8"))
        st = video_path.stat()
        return meta.get("size") == st.st_size and meta.get("mtime_ns") == st.st_mtime_ns
    except Exception:
        return False


def extract_audio(video_path: Path, out_path: Path, ffmpeg_path: str = "", force: bool = False) -> Path:
    """Extract 16kHz mono PCM WAV from ``video_path`` into ``out_path``.

    FunASR's paraformer expects 16kHz mono. Idempotent via a sidecar
    (``<out>.src.json`` recording the source's size+mtime): re-runs skip
    re-extraction only when the source video is unchanged; ``force`` always
    re-extracts.
    """
    video_path = Path(video_path)
    out_path = Path(out_path)
    if not video_path.exists():
        raise RuntimeError(f"Video file not found: {video_path}")
    if not force and _cache_hit(video_path, out_path):
        logger.info("Audio already extracted, reusing: %s", out_path)
        return out_path

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
    # text=True without an encoding uses the ANSI codepage (GBK on zh-CN
    # Windows); ffmpeg echoes the video path in its banner, so a Chinese
    # filename crashes the stderr reader thread with UnicodeDecodeError (the
    # extraction itself still succeeds - the traceback is just noise on
    # stderr). Decode as UTF-8 with replacement instead.
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        tail = (result.stderr or "")[-500:]
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {tail}")
    if not out_path.exists():
        raise RuntimeError(f"ffmpeg produced no output file: {out_path}")
    st = video_path.stat()
    _sidecar_path(out_path).write_text(
        json.dumps({"source": str(video_path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}),
        encoding="utf-8",
    )
    return out_path
