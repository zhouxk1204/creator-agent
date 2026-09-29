"""Render transcript segments as an SRT subtitle file.

Pure functions, no I/O beyond ``write_srt`` — usable from both the pipeline
and standalone tools.
"""

from __future__ import annotations

from pathlib import Path

from creator_agent.models.transcript import TranscriptSegment


def _fmt_timestamp(sec: float) -> str:
    """Seconds -> SRT timestamp ``HH:MM:SS,mmm``."""
    ms = max(0, round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list[TranscriptSegment]) -> str:
    """Build SRT content from segments. Empty-text segments are skipped and
    the remaining cues are renumbered sequentially."""
    cues: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        cues.append(f"{len(cues) + 1}\n{_fmt_timestamp(seg.start)} --> {_fmt_timestamp(seg.end)}\n{text}\n")
    return "\n".join(cues)


def write_srt(segments: list[TranscriptSegment], path: Path) -> Path:
    """Write segments as SRT to ``path`` (UTF-8, trailing newline)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = segments_to_srt(segments)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")
    return path
