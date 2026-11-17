"""Raw JA timing checkpoints and inspection cues (stdlib only).

Shared with the standalone worker through its existing sys.path shim. Inspection
uses ungated aligner values, never VAD estimates, time repairs, or display polish.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex[:8]


def _json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        # Keep invalid model values visible while producing strict JSON.
        return {"nonfinite_float": repr(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def save_checkpoint(audit: dict | None, stage: str | None = None) -> None:
    """Atomically update THIS run's checkpoint; never reuse another run's path."""
    if audit is None:
        return
    if stage is not None:
        audit["stage"] = stage
    if not audit.get("checkpoint_path"):
        return
    path = Path(audit["checkpoint_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as f:
            tmp_path = Path(f.name)
            json.dump(_json_value(audit), f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def audit_chunk(audit: dict | None, idx: int) -> dict | None:
    if audit is None:
        return None
    return next((c for c in audit["chunks"] if c["idx"] == idx), None)


def _valid_range(start, end, limit: float | None) -> bool:
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    return (
        math.isfinite(start)
        and math.isfinite(end)
        and 0 <= start < end
        and (limit is None or end <= limit + 1e-9)
        and round(end * 1000) > round(start * 1000)
    )


def inspection_cues(audit: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Readable chunk spans and word cues from RAW global aligner values.

    A chunk's span is its first word's start and last word's end, not a new
    forced alignment of raw_text. Its displayed text is the actual aligner
    input (which the existing repeat-tail guard may have shortened). Invalid
    ranges are recorded as omissions, not stretched or silently dropped.
    """
    chunk_cues, word_cues, warnings = [], [], []
    limit = audit.get("duration_sec")
    for chunk in audit.get("chunks", []):
        rec = chunk.get("raw_alignment") or {}
        words = rec.get("words") or []
        identity = {"chunk": chunk["idx"], "pass": chunk["pass"]}
        if not rec.get("ok") or not words:
            warnings.append({**identity, "reason": "NO_RAW_ALIGNMENT", "detail": rec.get("error")})
            continue
        if chunk.get("raw_text") != chunk.get("alignment_text"):
            warnings.append({**identity, "reason": "ALIGNMENT_TEXT_DIFFERS_FROM_RAW_ASR"})
        start, end = words[0][1], words[-1][2]
        if _valid_range(start, end, limit):
            chunk_cues.append({"start": start, "end": end, "text": chunk["alignment_text"], **identity})
        else:
            warnings.append({**identity, "reason": "INVALID_CHUNK_SPAN", "start": start, "end": end})
        for wi, word in enumerate(words):
            text, start, end = word
            if not str(text).strip() or not _valid_range(start, end, limit):
                warnings.append({**identity, "word": wi, "reason": "UNRENDERABLE_RAW_WORD", "raw_word": word})
                continue
            word_cues.append({"start": start, "end": end, "text": text, **identity, "word": wi})
    for label, cues in (("chunk", chunk_cues), ("word", word_cues)):
        # Sorting for playback does not alter any raw start/end value.
        cues.sort(key=lambda c: (c["start"], c["end"], c["chunk"]))
        for prev, cur in zip(cues, cues[1:]):
            if cur["start"] < prev["end"]:
                warnings.append({"reason": "RAW_OVERLAP", "view": label, "left": prev, "right": cur})
    return chunk_cues, word_cues, warnings


def raw_asr_text(audit: dict) -> str:
    lines = []
    for chunk in audit.get("chunks", []):
        lines.append(f"[{chunk['pass']} chunk {chunk['idx'] + 1} {chunk['start']:.3f}-{chunk['end']:.3f}]")
        if chunk.get("asr_status") == "failed":
            lines.append(f"[ASR FAILED] {chunk.get('asr_error', '')}")
        else:
            lines.append(chunk.get("raw_text") or "")
    return "\n".join(lines) + "\n"
