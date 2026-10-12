"""Post-ASR boundary check of subtitle cues against the ORIGINAL video+audio.

The ASR worker only ever sees the extracted vocal stem — nothing verifies its
cue timeline against the original media. This module closes that gap, in the
main env with no heavy deps (ffmpeg/ffprobe only):

1. ``probe_video_duration`` — ffprobe the original video's container duration
   and compare it with the extracted audio's duration (a shorter extraction
   means ffmpeg truncated the audio and the whole timeline is short).
2. ``detect_silences`` — ffmpeg ``silencedetect`` over the vocal stem (falls
   back to the extracted mix) to get true silence intervals.
3. ``refine_cues`` — pure function: cue edges that sit inside a silence (or
   within ``snap_window`` of a silence edge) are snapped to the speech
   onset/offset (start = onset - lead_in, end = offset + tail_out); edges
   landing mid-speech are normal (cues split at punctuation inside an
   utterance) and left alone. Suspicious cases are flagged for human review:
   ``START_IN_SILENCE`` / ``END_IN_SILENCE`` (edge deep inside a silence),
   ``NO_SPEECH`` (cue covers almost no speech), ``EXCEEDS_VIDEO`` (clamped to
   the video duration).

The .srt itself stays clean (it is imported into 剪映 for eyeballing); flags
go to a separate ``<stem>.check.txt`` report written by the caller.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from creator_agent.asr.audio_extractor import find_ffmpeg

logger = logging.getLogger(__name__)

# Flag reasons (stable strings — they appear in logs and the .check.txt report).
# A snap that succeeds within snap_window is a TRUSTED correction, not a
# suspect — flags are reserved for cases the check could not confidently fix.
START_IN_SILENCE = "START_IN_SILENCE"  # cue starts deep inside a silence (couldn't snap to the onset)
END_IN_SILENCE = "END_IN_SILENCE"  # cue ends deep inside a silence (subtitle hangs over silence)
NO_SPEECH = "NO_SPEECH"  # cue covers almost no speech at all (hallucination / misalignment)
EXCEEDS_VIDEO = "EXCEEDS_VIDEO"  # cue extends past the video duration (clamped)
AUDIO_VIDEO_MISMATCH = "AUDIO_VIDEO_MISMATCH"  # extracted audio shorter than the video (truncated?)

_AV_MISMATCH_SEC = 1.0  # audio shorter than video by more than this -> AUDIO_VIDEO_MISMATCH
_EXCEEDS_TOLERANCE = 0.05  # small float slack before a cue counts as past the video end
_MIN_SPEECH_COV = 0.3  # cue covering less speech than this gets NO_SPEECH

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")


def find_ffprobe(ffmpeg_path: str = "") -> str:
    """Resolve ffprobe: it ships next to ffmpeg in full builds, else PATH."""
    if ffmpeg_path:
        candidate = Path(ffmpeg_path).with_name(Path(ffmpeg_path).name.replace("ffmpeg", "ffprobe"))
        if candidate.exists():
            return str(candidate)
    found = shutil.which("ffprobe")
    if not found:
        raise RuntimeError("ffprobe not found (looked next to ffmpeg and on PATH)")
    return found


def probe_video_duration(video_path: Path, ffmpeg_path: str = "") -> float:
    """Container duration of ``video_path`` in seconds, via ffprobe."""
    ffprobe = find_ffprobe(ffmpeg_path)
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed (exit {result.returncode}): {(result.stderr or '')[-300:]}")
    return float(result.stdout.strip())


def parse_silencedetect(stderr_text: str, duration: float | None = None) -> list[tuple[float, float]]:
    """Parse ffmpeg silencedetect stderr into silence intervals [(start, end)].

    A trailing silence at EOF has no ``silence_end`` line; it is closed at
    ``duration`` when given (otherwise dropped)."""
    silences: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in stderr_text.splitlines():
        m = _SILENCE_START.search(line)
        if m:
            open_start = max(0.0, float(m.group(1)))
            continue
        m = _SILENCE_END.search(line)
        if m and open_start is not None:
            end = float(m.group(1))
            if end > open_start:
                silences.append((open_start, end))
            open_start = None
    if open_start is not None and duration is not None and duration > open_start:
        silences.append((open_start, duration))
    return silences


def detect_silences(
    audio_path: Path,
    ffmpeg_path: str = "",
    noise_db: float = -35.0,
    min_silence_sec: float = 0.12,
    duration: float | None = None,
) -> list[tuple[float, float]]:
    """Silence intervals in ``audio_path`` via ffmpeg's silencedetect filter.

    Run on the vocal stem when available (music in the mix can mask pauses).
    """
    ffmpeg = find_ffmpeg(ffmpeg_path)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f",
        "null",
        "-",
    ]
    # silencedetect reports on stderr; a nonzero exit means a real IO/codec error.
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg silencedetect failed (exit {result.returncode}): {(result.stderr or '')[-300:]}")
    return parse_silencedetect(result.stderr or "", duration)


def _silence_containing(silences: list[tuple[float, float]], t: float) -> tuple[float, float] | None:
    for s, e in silences:
        if s < t < e:
            return (s, e)
    return None


def _speech_coverage(start: float, end: float, silences: list[tuple[float, float]]) -> float:
    """Fraction of ``[start, end]`` NOT covered by silence (0..1)."""
    if end <= start:
        return 0.0
    sil = sum(max(0.0, min(end, e) - max(start, s)) for s, e in silences)
    return max(0.0, min(1.0, 1.0 - sil / (end - start)))


def refine_cues(
    cues: list[dict],
    silences: list[tuple[float, float]],
    audio_duration: float,
    video_duration: float | None = None,
    snap_window: float = 0.5,
    lead_in: float = 0.08,
    tail_out: float = 0.10,
    min_dur: float = 0.2,
) -> tuple[list[dict], list[dict]]:
    """Snap cue edges to nearby silence-implied speech edges; flag suspects.

    ``cues`` = ``[{"start", "end", "text", ...}, ...]`` (absolute seconds, time
    order); extra keys (e.g. ``speaker``) are preserved. Returns ``(refined,
    flags)`` where ``flags`` = ``[{"index", "reasons", "detail"}, ...]`` for
    suspicious cues, plus a single ``{"index": None, ...}`` entry when the
    extracted audio is shorter than the video (AUDIO_VIDEO_MISMATCH).

    Snapping rules (per edge, independent) — a cue edge landing mid-speech is
    NORMAL (cues split at punctuation inside an utterance), so mid-speech
    edges are only polished when a silence edge lies within ``snap_window``,
    and never flagged:

      start: inside silence (s,e) -> e - ``lead_in`` (speech onset); else the
             nearest silence-end within ``snap_window``. If the containing
             silence is so deep the shift exceeds ``snap_window``, keep the
             original time and flag START_IN_SILENCE.
      end:   mirror — inside silence (s,e) -> s + ``tail_out`` (speech
             offset); else the nearest silence-start within ``snap_window``;
             deep-silence -> keep + END_IN_SILENCE.

    Flags beyond that: NO_SPEECH (cue covers almost no speech —
    hallucination/misalignment), EXCEEDS_VIDEO (clamped to the video
    duration). Afterward cues are re-made monotonic, non-overlapping and
    >= ``min_dur``.
    """
    silences = sorted((max(0.0, float(s)), float(e)) for s, e in silences if e > s)
    limit = audio_duration
    if video_duration is not None:
        limit = min(limit, video_duration)

    flags: list[dict] = []
    if video_duration is not None and audio_duration < video_duration - _AV_MISMATCH_SEC:
        flags.append(
            {
                "index": None,
                "reasons": [AUDIO_VIDEO_MISMATCH],
                "detail": f"audio {audio_duration:.3f}s is {video_duration - audio_duration:.1f}s shorter "
                f"than video {video_duration:.3f}s — extraction may be truncated",
            }
        )

    def snap_edge(t: float, is_start: bool) -> tuple[float, str | None, str]:
        """Returns (new_t, flag_reason|None, detail)."""
        containing = _silence_containing(silences, t)
        if containing is not None:
            s, e = containing
            target = max(0.0, e - lead_in) if is_start else s + tail_out
            if abs(target - t) <= snap_window:
                return target, None, ""
            reason = START_IN_SILENCE if is_start else END_IN_SILENCE
            return (
                t,
                reason,
                f"edge {t - s:.2f}s into a {e - s:.2f}s silence (snap {target - t:+.2f}s > {snap_window}s)",
            )
        # Mid-speech edge: polish only when a silence edge lies within the window.
        if is_start:
            cands = [e for s, e in silences if abs(e - lead_in - t) <= snap_window]
            if cands:
                return max(0.0, min(cands, key=lambda e: abs(e - lead_in - t)) - lead_in), None, ""
        else:
            cands = [s for s, e in silences if abs(s + tail_out - t) <= snap_window]
            if cands:
                return min(cands, key=lambda s: abs(s + tail_out - t)) + tail_out, None, ""
        return t, None, ""

    refined: list[dict] = []
    for i, cue in enumerate(cues):
        start, end = float(cue["start"]), float(cue["end"])
        reasons: list[str] = []
        detail: list[str] = []

        new_start, reason, d = snap_edge(start, is_start=True)
        if reason:
            reasons.append(reason)
            detail.append("start: " + d)
        start = new_start

        new_end, reason, d = snap_edge(end, is_start=False)
        if reason:
            reasons.append(reason)
            detail.append("end: " + d)
        end = new_end

        if _speech_coverage(start, end, silences) < _MIN_SPEECH_COV:
            reasons.append(NO_SPEECH)
            detail.append("cue covers almost no speech")

        if video_duration is not None and end > video_duration + _EXCEEDS_TOLERANCE:
            reasons.append(EXCEEDS_VIDEO)
            detail.append(f"end {end:.2f}s past video {video_duration:.2f}s (clamped)")
        start = min(max(start, 0.0), limit)
        end = min(max(end, 0.0), limit)

        if reasons:
            flags.append({"index": i, "reasons": sorted(set(reasons)), "detail": "; ".join(detail)})
        refined.append({**cue, "start": round(start, 3), "end": round(end, 3)})

    # Re-enforce monotonic, non-overlapping, non-empty ranges (snapping two
    # adjacent cues into the same gap could otherwise make them collide).
    prev_end = 0.0
    for cue in refined:
        cue["start"] = max(cue["start"], prev_end)
        cue["end"] = max(cue["end"], cue["start"] + min_dur)
        cue["end"] = min(cue["end"], limit)
        cue["start"] = min(cue["start"], max(0.0, cue["end"] - min_dur))
        prev_end = cue["end"]
    return refined, flags


def format_check_report(
    stem: str,
    video_duration: float | None,
    audio_duration: float,
    cue_count: int,
    snapped_count: int,
    flags: list[dict],
    cues: list[dict],
    uncovered: list | None = None,
) -> str:
    """Human-readable boundary-check report (written to ``<stem>.check.txt``).

    ``uncovered`` = [(start, end), ...] speech ranges that ended up with no
    subtitle even after the recovery pass (if enabled) — the "needs a human
    ear" list, flagged UNRECOVERED_SPEECH."""

    def ts(sec: float) -> str:
        m, s = divmod(max(0.0, sec), 60.0)
        return f"{int(m):02d}:{s:06.3f}"

    video_dur = ts(video_duration) if video_duration is not None else "(ffprobe 不可用)"
    suspect = sum(1 for f in flags if f["index"] is not None)
    lines = [
        f"边界检查报告: {stem}",
        f"video duration : {video_dur}",
        f"audio duration : {ts(audio_duration)}",
        f"cues           : {cue_count}（吸附调整 {snapped_count} 条，可疑 {suspect} 条）",
        "",
    ]
    video_flags = [f for f in flags if f["index"] is None]
    cue_flags = [f for f in flags if f["index"] is not None]
    for f in video_flags:
        lines.append(f"[{'/'.join(f['reasons'])}] {f['detail']}")
    for f in cue_flags:
        cue = cues[f["index"]]
        lines.append(
            f"[{'/'.join(f['reasons'])}] cue #{f['index'] + 1} {ts(cue['start'])} --> {ts(cue['end'])} "
            f"{cue.get('text', '')[:20]} — {f['detail']}"
        )
    if uncovered:
        lines.append("")
        lines.append(f"有语音但无字幕的区间（补漏后仍为空，需人工听）: {len(uncovered)} 段")
        for s, e in uncovered:
            lines.append(f"[UNRECOVERED_SPEECH] {ts(float(s))} --> {ts(float(e))} ({float(e) - float(s):.1f}s)")
    if not flags and not uncovered:
        lines.append("所有 cue 边界均落在静音附近，无可疑项。")
    return "\n".join(lines) + "\n"
