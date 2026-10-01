"""Render transcript segments as an SRT subtitle file, and parse SRT back.

Pure functions, no I/O beyond ``write_srt`` / ``parse_srt`` — usable from
both the pipeline and standalone tools.
"""

from __future__ import annotations

import re
from pathlib import Path

from creator_agent.models.transcript import TranscriptSegment

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")

# Short interjection cues add nothing a viewer can't hear themselves — drop
# them from subtitles (the full text stays in .txt / .transcript.json).
# Match is on the text with punctuation/long-vowel marks stripped and repeated
# chars collapsed, so ああ/ええ/うーん/はいはい all reduce to an entry below.
_FILLER_STRIP = "。、！？!?.,…‥・「」『』\"' 　\t\n"
_FILLERS = {
    "うん",
    "はい",
    "おう",
    "ふん",
    "へえ",
    "うわ",
    "わあ",
    "やあ",
    "ほう",
    "うんうん",
    "はいはい",
    "ん",
    "あ",
    "え",
    "お",
    "う",
}


def is_filler_cue(text: str) -> bool:
    """True when a cue is just a short interjection (うん。/はい。/あっ …).

    Single-character utterances and a small set of common acknowledgments are
    considered filler; anything longer or content-bearing is kept.
    """
    t = text.strip().strip(_FILLER_STRIP)
    t = t.replace("ー", "").replace("〜", "").replace("~", "")
    t = t.rstrip("っッ")  # あっ/えっ -> あ/え
    t = re.sub(r"(.)\1+", r"\1", t)  # collapse repeated chars: ああ -> あ
    if not t:
        return True
    if len(t) == 1:
        return True
    return t in _FILLERS


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
    """Build SRT content from segments. Empty-text and short-filler cues are
    skipped and the remaining cues are renumbered sequentially. Speaker
    labels are NOT rendered into subtitles (they stay in .txt /
    .transcript.json only)."""
    cues: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if not text or is_filler_cue(text):
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
