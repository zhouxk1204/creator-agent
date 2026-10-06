#!/usr/bin/env python
"""Qwen3-ForcedAligner word-level alignment worker — runs in the dedicated
``creator-asr-ja-aligner`` conda env (transformers 4.57.6 + qwen-asr).

This is a SEPARATE env from ``creator-asr-ja`` because qwen-asr pins
transformers==4.57.6 while the Qwen3-ASR model needs transformers>=5.13 —
the two cannot coexist, so alignment is shelled out to this worker as a
subprocess (one call per video, model loaded once).

Standalone: imports ONLY qwen_asr / torch + stdlib (no creator_agent — that
package is not installed in this env). Invoked by ja_worker._align_batch.

Usage:
    python aligner_worker.py --jobs align_jobs.json
        --model C:/models/Qwen3-ForcedAligner-0.6B
        [--device cuda:0] [--language Japanese]

align_jobs.json = [{"chunk_id": int, "wav": str, "text": str,
                    "chunk_start": float}, ...]
  wav         16kHz mono WAV of ONE ASR chunk (<= aligner's 5-min limit).
  text        the chunk's ASR transcript (alignment is forced, not free).
  chunk_start absolute offset (s) added to the chunk-relative word times.

Prints results wrapped in sentinel markers (same contract as ja_worker.py):

    ===ALIGN_RESULTS_BEGIN===
    [{"chunk_id": int, "ok": true,  "words": [[text, start, end], ...]},
     {"chunk_id": int, "ok": false, "words": null, "error": str}, ...]
    ===ALIGN_RESULTS_END===

Per-chunk failures set ok:false with words:null (caller falls back to
proportional cue times for that chunk); they never abort the batch. Exit 0.

In addition to the final sentinel block, each chunk's result is ALSO printed
the moment it finishes as a single line::

    ===ALIGN_CHUNK=== {"chunk_id": int, "ok": bool, "words": ...|null, ...}

so the caller can run its quality gate + cue splitting per chunk as a
pipeline instead of waiting for the whole video to align.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

_RESULT_BEGIN = "===ALIGN_RESULTS_BEGIN==="
_RESULT_END = "===ALIGN_RESULTS_END==="
_CHUNK_MARKER = "===ALIGN_CHUNK=== "


def _emit(results: list) -> None:
    print(_RESULT_BEGIN, flush=True)
    print(json.dumps(results, ensure_ascii=False), flush=True)
    print(_RESULT_END, flush=True)


def _warn(msg: str) -> None:
    print(f"[ja-aligner] WARNING: {msg}", file=sys.stderr, flush=True)


def _progress(msg: str) -> None:
    """Progress on stderr — ja_worker forwards it live to the console."""
    print(f"[ja-aligner] {msg}", file=sys.stderr, flush=True)


def _load_aligner(model_id: str, device: str):
    import torch
    from qwen_asr import Qwen3ForcedAligner

    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    return Qwen3ForcedAligner.from_pretrained(model_id, dtype=dtype, device_map=device)


def _align_one(aligner, job: dict, language: str) -> list | None:
    """Word-level [(text, start, end)] in ABSOLUTE seconds (chunk_start added).

    Returns None on empty/failed alignment — caller degrades that chunk to
    proportional timing. Times from qwen_asr are relative to the chunk.
    """
    res = aligner.align(audio=job["wav"], text=job["text"], language=language)
    items = res[0]
    off = float(job["chunk_start"])
    words = [(w.text, float(w.start_time) + off, float(w.end_time) + off) for w in items]
    return words or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True, help="path to align_jobs.json")
    parser.add_argument("--model", required=True, help="ForcedAligner local path / HF id")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--language", default="Japanese")
    args = parser.parse_args()

    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    if not jobs:
        _emit([])
        return 0

    _progress(f"加载 ForcedAligner（{args.model}）…")
    try:
        aligner = _load_aligner(args.model, args.device)
        _progress("ForcedAligner 加载完成")
    except Exception as e:  # model load failure -> all chunks degrade, still emit JSON
        traceback.print_exc()
        _emit([{"chunk_id": j["chunk_id"], "ok": False, "words": None, "error": f"model load: {e}"} for j in jobs])
        return 0

    results: list[dict] = []
    n = len(jobs)
    for i, job in enumerate(jobs):
        t0 = time.monotonic()
        try:
            words = _align_one(aligner, job, args.language)
            if words is None:
                _warn(f"chunk {job['chunk_id']}: no words aligned; proportional timing")
            rec = {"chunk_id": job["chunk_id"], "ok": words is not None, "words": words}
            _progress(f"  对齐 {i + 1}/{n}（{time.monotonic() - t0:.1f}s）：{len(words) if words else 0} 词")
        except Exception as e:
            _warn(f"chunk {job['chunk_id']} align failed: {e}")
            rec = {"chunk_id": job["chunk_id"], "ok": False, "words": None, "error": str(e)}
        results.append(rec)
        # Stream this chunk's result immediately: the caller quality-checks
        # and cue-splits per chunk as the pipeline advances.
        print(_CHUNK_MARKER + json.dumps(rec, ensure_ascii=False), flush=True)

    _emit(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
