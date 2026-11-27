"""Stage 5 helpers: SRT formatting from timed subtitle events."""

from dataclasses import dataclass


class SrtError(Exception):
    """Subtitle events cannot be serialised into a valid SRT."""


@dataclass(frozen=True)
class SrtCue:
    start: float
    end: float
    text: str
    group_id: int


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        raise SrtError("SRT timestamps cannot be negative")
    total_ms = int(round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, milliseconds = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def build_srt(cues: list[SrtCue], *, minimum_duration: float = 0.04) -> str:
    """Serialise cues in time order, never emitting a zero/negative-length cue."""
    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end))
    blocks: list[str] = []
    for position, cue in enumerate(ordered, start=1):
        start = max(0.0, cue.start)
        end = max(cue.end, start + minimum_duration)
        if position < len(ordered):
            following = ordered[position]
            if following.start > start and end > following.start:
                end = following.start
        if end <= start:
            raise SrtError(f"Subtitle cue {cue.group_id} has an invalid time range")
        blocks.append(f"{position}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{cue.text}\n")
    return "\n".join(blocks)
