"""Render transcript segments as an SRT subtitle file, and parse SRT back.

Pure functions, no I/O beyond ``write_srt`` / ``parse_srt`` — usable from
both the pipeline and standalone tools.
"""

from __future__ import annotations

import re
from pathlib import Path

from creator_agent.models.transcript import TranscriptSegment

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _fmt_timestamp(sec: float) -> str:
    """Seconds -> SRT timestamp ``HH:MM:SS,mmm``."""
    ms = max(0, round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_timestamp(ts: str) -> float:
    """SRT timestamp ``HH:MM:SS,mmm`` -> seconds."""
    m = _TS.search(ts)
    if not m:
        raise ValueError(f"bad SRT timestamp: {ts!r}")
    h, mi, s, ms = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def parse_srt(path: Path) -> list[TranscriptSegment]:
    """Parse an SRT file into segments (order preserved; cue text keeps its
    line breaks). Tolerates CRLF and a missing trailing blank line."""
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    segments: list[TranscriptSegment] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        # Locate the time-range line; anything above it (the cue index) is ignored.
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ti is None:
            continue
        start_s, end_s = (t.strip() for t in lines[ti].split("-->", 1))
        text = "\n".join(lines[ti + 1 :]).strip()
        if text:
            segments.append(TranscriptSegment(start=_parse_timestamp(start_s), end=_parse_timestamp(end_s), text=text))
    return segments


def segments_to_srt(segments: list[TranscriptSegment]) -> str:
    """Build SRT content from segments. Empty-text segments are skipped and
    the remaining cues are renumbered sequentially."""
    cues: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if seg.speaker:
            text = f"{seg.speaker}: {text}"
        cues.append(f"{len(cues) + 1}\n{_fmt_timestamp(seg.start)} --> {_fmt_timestamp(seg.end)}\n{text}\n")
    return "\n".join(cues)


def write_srt(segments: list[TranscriptSegment], path: Path) -> Path:
    """Write segments as SRT to ``path`` (UTF-8, trailing newline)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = segments_to_srt(segments)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")
    return path
