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
    """Build SRT content from segments. Every recognized cue is kept (short
    interjections like うん。included — full recognition, no filler filter);
    only empty-text cues are skipped, and cues are renumbered sequentially.
    Speaker labels are NOT rendered into subtitles (they stay in .txt /
    .transcript.json only)."""
    cues: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        cues.append(f"{len(cues) + 1}\n{_fmt_timestamp(seg.start)} --> {_fmt_timestamp(seg.end)}\n{text}\n")
    return "\n".join(cues)


def finalize_for_srt(
    cues: list[dict],
    min_display_sec: float = 0.0,
    lead_in_sec: float = 0.0,
    tail_sec: float = 0.0,
    limit: float | None = None,
) -> list[dict]:
    """Display-time polish applied to .srt ONLY (transcript.json / .txt keep
    the real speech times). Pure function over cue dicts ({"start", "end", ...},
    time order); extra keys (text, speaker) are preserved.

    - ``lead_in_sec`` / ``tail_sec``: show each cue a bit earlier / longer.
      Keep these 0 when the boundary check already snapped edges with its own
      lead_in/tail_out margins — enabling both would double the margin.
    - ``min_display_sec``: stretch cues too short to read.
    Every cue still ends before the next cue's (adjusted) start minus 20ms,
    and is clamped to ``limit`` (media duration) when given. All-zero params
    return the input unchanged (identity = old behavior).
    """
    if not cues or (min_display_sec <= 0 and lead_in_sec <= 0 and tail_sec <= 0):
        return cues
    starts = [max(0.0, float(c["start"]) - lead_in_sec) for c in cues]
    out: list[dict] = []
    for i, c in enumerate(cues):
        s = starts[i]
        e = float(c["end"]) + tail_sec
        if min_display_sec > 0 and e - s < min_display_sec:
            e = s + min_display_sec
        if i + 1 < len(cues):
            e = min(e, starts[i + 1] - 0.02)
        if limit is not None:
            e = min(e, limit)
        e = max(e, s + 0.05)
        out.append({**c, "start": round(s, 3), "end": round(e, 3)})
    return out


def write_srt(segments: list[TranscriptSegment], path: Path) -> Path:
    """Write segments as SRT to ``path`` (UTF-8, trailing newline)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = segments_to_srt(segments)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")
    return path
