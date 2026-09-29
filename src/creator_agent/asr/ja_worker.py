#!/usr/bin/env python
"""Japanese ASR worker (vocal separation + Qwen3-ASR) - runs in the dedicated
``creator-asr-ja`` conda env.

Standalone: imports ONLY audio_separator / transformers / torch / silero_vad /
soundfile / scipy + stdlib (no creator_agent - that package is not installed
in this env). Invoked as a subprocess by the main env's JaTranscriber.

Usage:
    python ja_worker.py --jobs jobs.json [--model Qwen/Qwen3-ASR-1.7B-hf]
        [--sep-model ""] [--device cuda:0] [--language Japanese] [--keep-vocals]

jobs.json = [{"video_id": str, "audio_path": str}, ...]
(audio_path must be 16kHz mono WAV, produced by the caller's extract_audio.)

Pipeline per job: audio-separator vocal isolation -> silero-vad speech chunks
-> Qwen3-ASR per chunk -> segments with chunk-boundary timestamps. Loads all
models ONCE for the whole batch. Prints results wrapped in sentinel markers
(identical contract to asr/worker.py):

    ===ASR_RESULTS_BEGIN===
    [{"video_id":..., "ok":true, "text":..., "segments":[{"start","end","text"}],
      "duration_sec":..., "vocals_path":...}, ...]
    ===ASR_RESULTS_END===

Per-video failures set ``ok: false`` with an ``error`` field; they never abort
the batch. Exit code 0 even if some videos failed (the caller reads the JSON).

Model caches: HF models -> ~/.cache/huggingface (set HF_ENDPOINT for mirrors);
separation model -> ~/.cache/audio-separator-models.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import traceback
from pathlib import Path

_RESULT_BEGIN = "===ASR_RESULTS_BEGIN==="
_RESULT_END = "===ASR_RESULTS_END==="

_SAMPLE_RATE = 16000  # silero-vad + Qwen3-ASR both want 16 kHz mono


def _emit(results: list) -> None:
    """Print results wrapped in begin/end markers on their own lines."""
    print(_RESULT_BEGIN, flush=True)
    print(json.dumps(results, ensure_ascii=False), flush=True)
    print(_RESULT_END, flush=True)


# ---------------------------------------------------------------------------
# Model loading (once per batch)
# ---------------------------------------------------------------------------


def _load_separator(sep_model: str):
    from audio_separator.separator import Separator

    model_dir = str(Path.home() / ".cache" / "audio-separator-models")
    separator = Separator(model_file_dir=model_dir, output_format="WAV")
    if sep_model:
        separator.load_model(model_filename=sep_model)
    else:
        separator.load_model()  # default BS-RoFormer vocal model
    return separator


def _load_vad():
    from silero_vad import load_silero_vad

    return load_silero_vad()


def _load_asr(model_id: str, device: str):
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    processor = AutoProcessor.from_pretrained(model_id)
    if device == "cpu":
        model = AutoModelForMultimodalLM.from_pretrained(model_id, torch_dtype="auto")
        model = model.to("cpu")
    else:
        model = AutoModelForMultimodalLM.from_pretrained(model_id, torch_dtype="auto", device_map={"": device})
    return processor, model


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def _separate_vocals(separator, audio_path: Path, out_dir: Path) -> Path:
    """Isolate the vocal stem; returns the vocals WAV path."""
    outputs = separator.separate(str(audio_path))
    vocals = [Path(p) for p in outputs if "vocal" in Path(p).stem.lower()]
    if not vocals:
        raise RuntimeError(f"separator produced no vocal stem: {outputs}")
    # Normalize to 16kHz mono (separation models usually output 44.1kHz).
    return _to_16k_mono(vocals[0], out_dir / "vocals_16k.wav")


def _to_16k_mono(wav_path: Path, out_path: Path) -> Path:
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(wav_path), always_2d=True)
    mono = data.mean(axis=1)
    if sr != _SAMPLE_RATE:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(sr, _SAMPLE_RATE)
        mono = resample_poly(mono, _SAMPLE_RATE // g, sr // g)
    sf.write(str(out_path), mono.astype(np.float32), _SAMPLE_RATE)
    return out_path


def _speech_chunks(vad_model, vocals_path: Path, duration: float) -> list[tuple[float, float]]:
    """silero-vad speech intervals -> merged ASR chunks [(start, end), ...]."""
    import soundfile as sf
    import torch
    from silero_vad import get_speech_timestamps

    # Local import shim: vad_chunker lives in creator_agent, not installed here.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vad_chunker import merge_speech_intervals  # noqa: E402

    data, _ = sf.read(str(vocals_path))
    wav = torch.from_numpy(data).float()
    ts = get_speech_timestamps(
        wav,
        vad_model,
        sampling_rate=_SAMPLE_RATE,
        min_speech_duration_ms=250,
        min_silence_duration_ms=300,
    )
    intervals = [(t["start"] / _SAMPLE_RATE, t["end"] / _SAMPLE_RATE) for t in ts]
    return merge_speech_intervals(intervals, max_gap=0.3, max_chunk=30.0, pad=0.1, duration=duration)


def _transcribe_chunk(processor, model, chunk_wav: Path, language: str) -> str:
    inputs = processor.apply_transcription_request(
        audio=str(chunk_wav),
        language=language,
    ).to(model.device, model.dtype)
    output_ids = model.generate(**inputs, max_new_tokens=512)
    generated = output_ids[:, inputs["input_ids"].shape[1] :]
    return processor.decode(generated, return_format="transcription_only")[0].strip()


def refine_timestamps(segments: list[dict], vocals_path: Path) -> list[dict]:
    """Seam for Qwen3-ForcedAligner word-level refinement (not implemented).

    Currently VAD chunk boundaries are used as-is. A future aligner pass would
    take (segments, vocals_path) and tighten each segment's start/end.
    """
    return segments


# ---------------------------------------------------------------------------
# Job driver
# ---------------------------------------------------------------------------


def _process_one(models, job: dict, args, keep_dir: Path | None) -> dict:
    import soundfile as sf

    separator, vad_model, processor, asr_model = models
    audio_path = Path(job["audio_path"])
    duration = float(sf.info(str(audio_path)).duration)

    with tempfile.TemporaryDirectory(prefix="ja-asr-") as tmp:
        work = Path(tmp)
        vocals = _separate_vocals(separator, audio_path, work)
        chunks = _speech_chunks(vad_model, vocals, duration)

        segments: list[dict] = []
        for i, (start, end) in enumerate(chunks):
            chunk_wav = _slice_wav(vocals, work / f"chunk_{i:04d}.wav", start, end)
            text = _transcribe_chunk(processor, asr_model, chunk_wav, args.language)
            if text:
                segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})

        segments = refine_timestamps(segments, vocals)

        vocals_out = None
        if keep_dir is not None:
            keep_dir.mkdir(parents=True, exist_ok=True)
            vocals_out = keep_dir / f"{job['video_id']}.vocals.wav"
            vocals_out.write_bytes(vocals.read_bytes())

    return {
        "text": "".join(s["text"] for s in segments),
        "segments": segments,
        "duration_sec": round(duration, 3),
        "vocals_path": str(vocals_out) if vocals_out else None,
    }


def _slice_wav(vocals_path: Path, out_path: Path, start: float, end: float) -> Path:
    import soundfile as sf

    data, sr = sf.read(str(vocals_path))
    clip = data[int(start * sr) : int(end * sr)]
    sf.write(str(out_path), clip, sr)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True, help="path to jobs.json")
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B-hf")
    parser.add_argument("--sep-model", default="", help="audio-separator model filename (blank = default)")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--language", default="Japanese")
    parser.add_argument("--keep-vocals-dir", default="", help="dir to keep vocals.wav per video (blank = discard)")
    args = parser.parse_args()

    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    if not jobs:
        _emit([])
        return 0

    keep_dir = Path(args.keep_vocals_dir) if args.keep_vocals_dir else None

    try:
        models = (
            _load_separator(args.sep_model),
            _load_vad(),
            *_load_asr(args.model, args.device),
        )
    except Exception as e:  # model load failure -> all jobs fail, but we still emit JSON
        traceback.print_exc()
        results = [{"video_id": j["video_id"], "ok": False, "error": f"model load: {e}"} for j in jobs]
        _emit(results)
        return 0

    results: list[dict] = []
    for job in jobs:
        vid = job["video_id"]
        if not Path(job["audio_path"]).exists():
            results.append({"video_id": vid, "ok": False, "error": f"audio not found: {job['audio_path']}"})
            continue
        try:
            out = _process_one(models, job, args, keep_dir)
            results.append({"video_id": vid, "ok": True, **out})
        except Exception as e:
            traceback.print_exc()
            results.append({"video_id": vid, "ok": False, "error": str(e)})

    _emit(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
