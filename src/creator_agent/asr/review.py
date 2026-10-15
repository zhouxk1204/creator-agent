"""Per-video review file (``<stem>.review.json``) for the JA ASR pipeline.

Companion to the ``.srt``: every cue carries the pipeline diagnostics that
used to live only in the CHUNK nnn logs (chunk id/reason, timing source,
align-fallback reason, speech coverage, repeat-tail cut, boundary action),
plus empty ``question`` / ``expected`` fields the user fills while listening
(see docs/subtitle-issue-template.md). ``dropped`` (hallucination-filtered)
and ``uncovered`` (speech with no subtitle) entries carry ``question`` too —
"missed line" problems have no cue to annotate otherwise.

Rerun safety: a rerun (--force, different tuning) regenerates the file, but
hand-filled questions are NEVER silently lost — they are migrated onto the
new cues by text + start-time (±0.5s) match (cue *numbers* change across
parameter sets, so they are not a key); questions that match nothing are
preserved under ``meta.orphaned_questions``. A corrupt existing file is
renamed to ``.bak`` before a fresh write.

Pure functions + one file-writing entry point; no pipeline imports beyond
typing, so the worker contract stays the only cross-process surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from creator_agent.config import JaAsrSettings

# Time tolerance for migrating a hand-filled question onto a new-run cue.
_MIGRATE_TIME_TOL_SEC = 0.5

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


def _annotated(entries: list[dict]) -> list[dict]:
    return [e for e in entries if (e.get("question") or "").strip() or (e.get("expected") or "").strip()]


def has_annotations(review: dict) -> bool:
    """True when the user filled any question/expected anywhere in the file."""
    return any(_annotated(review.get(key) or []) for key in ("cues", "dropped", "uncovered"))


def _migrate_list(
    old_entries: list[dict],
    new_entries: list[dict],
    *,
    match_text: bool,
    orphans: list[dict],
    kind: str,
) -> int:
    """Transfer question/expected from annotated old entries onto new ones.

    Match = same text (when the entry has text) AND start within
    ±_MIGRATE_TIME_TOL_SEC. Each new entry absorbs at most one old
    annotation; unmatched old annotations are appended to ``orphans``.
    Returns the number of migrated annotations.
    """
    migrated = 0
    used: set[int] = set()
    for old in _annotated(old_entries):
        hit = None
        for i, new in enumerate(new_entries):
            if i in used:
                continue
            if match_text and (old.get("text") or "") != (new.get("text") or ""):
                continue
            if abs(float(old.get("start", 0.0)) - float(new.get("start", 0.0))) > _MIGRATE_TIME_TOL_SEC:
                continue
            hit = i
            break
        if hit is None:
            orphan = {k: old.get(k) for k in ("start", "end", "text", "question", "expected") if old.get(k)}
            orphans.append({"kind": kind, **orphan})
            continue
        new_entries[hit]["question"] = old.get("question", "")
        if old.get("expected") is not None:
            new_entries[hit]["expected"] = old.get("expected", "")
        used.add(hit)
        migrated += 1
    return migrated


def migrate_annotations(old: dict, new: dict) -> tuple[dict, int, int]:
    """Merge hand-filled annotations from ``old`` into ``new`` (in place on
    the new lists). Returns ``(new, migrated_count, orphaned_count)``;
    orphaned annotations are preserved under meta.orphaned_questions."""
    orphans: list[dict] = []
    migrated = _migrate_list(old.get("cues") or [], new["cues"], match_text=True, orphans=orphans, kind="cue")
    migrated += _migrate_list(
        old.get("dropped") or [], new["dropped"], match_text=True, orphans=orphans, kind="dropped"
    )
    migrated += _migrate_list(
        old.get("uncovered") or [], new["uncovered"], match_text=False, orphans=orphans, kind="uncovered"
    )
    if orphans:
        new["meta"]["orphaned_questions"] = orphans
    return new, migrated, len(orphans)


def write_review(path: Path, review: dict) -> str:
    """Write ``<stem>.review.json``, preserving existing hand-filled
    annotations. Returns a short status string for the progress log:
    ``fresh`` / ``overwritten`` (old file had no annotations) /
    ``migrated:N[,orphans:M]`` / ``fresh_after_bak`` (corrupt old file)."""
    path = Path(path)
    status = "fresh"
    if path.exists():
        old: dict | None = None
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            path.rename(path.with_suffix(".bak.json"))
            status = "fresh_after_bak"
        if old is not None:
            if has_annotations(old):
                review, moved, orphaned = migrate_annotations(old, review)
                status = f"migrated:{moved}" + (f",orphans:{orphaned}" if orphaned else "")
            else:
                status = "overwritten"
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status
