"""Burn a subtitle file into a video with ffmpeg's ``subtitles`` filter.

Requires an ffmpeg build with libass (the full builds have it). The filter's
subtitle path is passed as a bare filename with ``cwd`` set to the SRT's
directory — absolute Windows paths inside filter syntax need painful
drive-letter escaping, this sidesteps it entirely.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from creator_agent.config import TranslateSettings

logger = logging.getLogger(__name__)


def _progress(msg: str) -> None:
    print(f"[burn] {msg}", file=sys.stderr, flush=True)
    logger.info(msg)


def burn_subtitles(video_path: Path, srt_path: Path, out_path: Path, settings: TranslateSettings) -> Path:
    """Burn ``srt_path`` into ``video_path`` -> ``out_path`` (re-encodes
    video, copies audio). Returns ``out_path``."""
    video_path, srt_path, out_path = Path(video_path), Path(srt_path), Path(out_path)
    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (set translate.ffmpeg_path)")
    style = (
        f"FontName={settings.burn_font},FontSize={settings.burn_fontsize},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,"
        "Outline=1,Shadow=0,Alignment=2,MarginV=24"
    )
    vf = f"subtitles={srt_path.name}:force_style='{style}'"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-c:a",
        "copy",
        str(out_path),
    ]
    _progress(f"[{video_path.stem}] 烧录字幕中 → {out_path.name}")
    proc = subprocess.run(cmd, cwd=srt_path.parent, capture_output=True)
    if proc.returncode != 0 or not out_path.exists():
        tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"ffmpeg subtitle burn failed ({proc.returncode}):\n{tail}")
    _progress(f"[{video_path.stem}] 烧录完成")
    return out_path
