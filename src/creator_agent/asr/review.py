"""Per-video review file (``<stem>.review.<timestamp>.json``) for the JA ASR pipeline.

Companion to the ``.srt``: every cue carries the pipeline diagnostics that
used to live only in the CHUNK nnn logs (chunk id/reason, timing source,
align-fallback reason, speech coverage, repeat-tail cut, boundary action),
plus empty ``question`` / ``expected`` fields the user fills while listening
(see docs/subtitle-issue-template.md). ``dropped`` (hallucination-filtered)
and ``uncovered`` (speech with no subtitle) entries carry ``question`` too —
"missed line" problems have no cue to annotate otherwise.

Rerun policy: EVERY run writes a NEW timestamped file and never touches
previous ones — a ``--force`` rerun with different tuning therefore can't
destroy hand-filled annotations; the old file keeps its questions next to
the cues/diag of the run they were made against (cue numbers shift across
parameter sets, so an old file is only meaningful with its own run's cues).
Match a review file to a rerun's .srt by picking the latest timestamp.

Pure functions + one file-writing entry point; no pipeline imports beyond
typing, so the worker contract stays the only cross-process surface.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from creator_agent.config import JaAsrSettings

# Tuning knobs recorded in meta.overrides — enough to know which experiment
# produced this file without opening settings.yaml.
OVERRIDE_FIELDS = (
    "vad_threshold",
    "vad_min_speech_ms",
    "vad_min_silence_ms",
    "vad_speech_pad_ms",
    "max_cue_chars",
    "max_cue_sec",
    "pause_sec",
    "recover_missed",
    "recover_min_sec",
    "recover_vad_threshold",
    "repeat_tail_max_cps",
    "chunk_target_min_sec",
    "chunk_target_max_sec",
    "max_chunk_sec",
    "asr_max_new_tokens",
    "align_pad_sec",
    "align_min_coverage",
    "srt_min_display_sec",
    "srt_lead_in_sec",
    "srt_tail_sec",
    "boundary_check",
    "boundary_snap_sec",
    "boundary_silence_db",
    "boundary_min_silence_sec",
    "boundary_lead_in_sec",
    "boundary_tail_sec",
    "speaker_threshold",
)


def settings_overrides(settings: JaAsrSettings) -> dict[str, Any]:
    """The meta.overrides dict: current values of the tunable knobs."""
    return {k: getattr(settings, k) for k in OVERRIDE_FIELDS}


def build_review(
    *,
    source: str,
    duration_sec: float | None,
    model: str,
    overrides: dict[str, Any],
    stats: dict[str, Any] | None,
    segments: list[dict],
    srt_dicts: list[dict],
    dropped: list[dict],
    uncovered: list,
) -> dict:
    """Assemble the review structure.

    ``segments`` = real speech times (post boundary check); ``srt_dicts`` =
    the same cues after .srt display polish, same order — the two are zipped.
    ``id`` matches the .srt cue number exactly (write_srt numbers only cues
    with non-empty text, sequentially), so "cue #N in 剪映" == "id N here".
    """
    cues: list[dict] = []
    for seg, disp in zip(segments, srt_dicts):
        if not str(disp.get("text", "")).strip():
            continue  # write_srt skips these; keep ids aligned
        diag = dict(seg.get("diag") or {})
        diag.setdefault("boundary", None)
        cues.append(
            {
                "id": len(cues) + 1,
                "start": round(float(disp["start"]), 3),
                "end": round(float(disp["end"]), 3),
                "speech_start": round(float(seg["start"]), 3),
                "speech_end": round(float(seg["end"]), 3),
                "text": disp["text"],
                "speaker": seg.get("speaker"),
                "diag": diag,
                "question": "",
                "expected": "",
            }
        )
    return {
        "meta": {
            "source": source,
            "duration_sec": duration_sec,
            "model": model,
            "overrides": overrides,
            "summary": stats or {},
        },
        "cues": cues,
        "dropped": [
            {
                "start": round(float(d["start"]), 3),
                "end": round(float(d["end"]), 3),
                "text": d.get("text", ""),
                "speech_cov": d.get("speech_cov", 0.0),
                "question": "",
            }
            for d in dropped
        ],
        "uncovered": [
            {"start": round(float(u[0]), 3), "end": round(float(u[1]), 3), "question": ""} for u in uncovered
        ],
    }


def write_review(directory: Path, stem: str, review: dict, *, now: datetime | None = None) -> Path:
    """Write the review as ``<stem>.review.<YYYYmmdd-HHMMSS>.json`` inside
    ``directory``; returns the path. Never overwrites: each run gets a fresh
    timestamped file (a same-second collision gets a ``-2``/``-3`` suffix),
    so previous runs — including any hand-filled annotations — are kept."""
    directory = Path(directory)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stem}.review.{stamp}.json"
    n = 1
    while path.exists():
        n += 1
        path = directory / f"{stem}.review.{stamp}-{n}.json"
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
