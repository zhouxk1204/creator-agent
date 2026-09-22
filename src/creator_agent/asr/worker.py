#!/usr/bin/env python
"""FunASR transcription worker - runs in the dedicated ``creator-asr`` conda env.

Standalone: imports ONLY funasr + stdlib (no creator_agent - that package is not
installed in this env). Invoked as a subprocess by the main env's Transcriber.

Usage:
    python worker.py --jobs jobs.json [--model paraformer-zh] [--vad-model fsmn-vad]
                     [--punc-model ct-punc] [--device cuda:0]

jobs.json = [{"video_id": str, "audio_path": str}, ...]

Loads the AutoModel ONCE, transcribes each audio, prints a JSON payload wrapped in
sentinel markers so funasr's own stdout logging can't corrupt parsing:

    ===ASR_RESULTS_BEGIN===
    [{"video_id":..., "ok":true, "text":..., "segments":[{"start","end","text"}],
      "duration_sec":...}, ...]
    ===ASR_RESULTS_END===

Per-video failures set ``ok: false`` with an ``error`` field; they never abort the
batch. Exit code 0 even if some videos failed (the caller reads the JSON).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

_RESULT_BEGIN = "===ASR_RESULTS_BEGIN==="
_RESULT_END = "===ASR_RESULTS_END==="


def _emit(results: list) -> None:
    """Print results wrapped in begin/end markers on their own lines."""
    print(_RESULT_BEGIN, flush=True)
    print(json.dumps(results, ensure_ascii=False), flush=True)
    print(_RESULT_END, flush=True)


# Sentence-ending punctuation used to group per-char timestamps into segments.
_SENT_END = set("。！？!?；;\n")


def _load_model(model: str, vad_model: str, punc_model: str, device: str):
    from funasr import AutoModel

    kwargs = {"model": model, "device": device}
    if vad_model:
        kwargs["vad_model"] = vad_model
    if punc_model:
        kwargs["punc_model"] = punc_model
    return AutoModel(**kwargs)


def _parse_segments(text: str, timestamp: list) -> tuple[list[dict], float | None]:
    """Map funasr's per-char ``timestamp`` (ms) + ``text`` into sentence segments.

    Paraformer returns ``timestamp`` as ``[[start_ms, end_ms], ...]`` roughly
    one pair per character. We zip chars with pairs, then cut segments on
    sentence-ending punctuation. Falls back gracefully when counts mismatch.
    Returns (segments[{start,end,text}], duration_sec).
    """
    if not timestamp:
        return ([{"start": 0.0, "end": 0.0, "text": text}] if text else []), None

    pairs = [(float(s) / 1000.0, float(e) / 1000.0) for s, e in timestamp]
    duration = pairs[-1][1] if pairs else None

    chars = [c for c in text if not c.isspace()]
    n = min(len(chars), len(pairs))

    segments: list[dict] = []
    buf_chars: list[str] = []
    buf_start: float | None = None
    buf_end: float = 0.0

    def flush() -> None:
        nonlocal buf_chars, buf_start, buf_end
        if buf_chars and buf_start is not None:
            segments.append({"start": round(buf_start, 3), "end": round(buf_end, 3), "text": "".join(buf_chars)})
        buf_chars = []
        buf_start = None
        buf_end = 0.0

    for i in range(n):
        start, end = pairs[i]
        ch = chars[i]
        if buf_start is None:
            buf_start = start
        buf_end = end
        buf_chars.append(ch)
        if ch in _SENT_END:
            flush()

    # Flush any trailing segment.
    flush()

    # Char/pair count mismatch -> fallback: one segment with the full text.
    if not segments and text:
        segments.append({"start": 0.0, "end": duration or 0.0, "text": text})
    return segments, duration


def _transcribe_one(model, audio_path: str) -> dict:
    res = model.generate(input=audio_path, batch_size_s=300, disable_pbar=False)
    if not res:
        return {"text": "", "segments": [], "duration_sec": None}
    item = res[0]
    text = (item.get("text") or "").strip()
    timestamp = item.get("timestamp") or []
    segments, duration = _parse_segments(text, timestamp)
    return {"text": text, "segments": segments, "duration_sec": duration}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True, help="path to jobs.json")
    parser.add_argument("--model", default="paraformer-zh")
    parser.add_argument("--vad-model", default="fsmn-vad")
    parser.add_argument("--punc-model", default="ct-punc")
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()

    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    if not jobs:
        _emit([])
        return 0

    try:
        model = _load_model(args.model, args.vad_model, args.punc_model, args.device)
    except Exception as e:  # model load failure -> all jobs fail, but we still emit JSON
        traceback.print_exc()
        results = [{"video_id": j["video_id"], "ok": False, "error": f"model load: {e}"} for j in jobs]
        _emit(results)
        return 0

    results: list[dict] = []
    for job in jobs:
        vid = job["video_id"]
        audio = job["audio_path"]
        if not Path(audio).exists():
            results.append({"video_id": vid, "ok": False, "error": f"audio not found: {audio}"})
            continue
        try:
            out = _transcribe_one(model, audio)
            results.append(
                {
                    "video_id": vid,
                    "ok": True,
                    "text": out["text"],
                    "segments": out["segments"],
                    "duration_sec": out["duration_sec"],
                }
            )
        except Exception as e:
            traceback.print_exc()
            results.append({"video_id": vid, "ok": False, "error": str(e)})

    _emit(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
