#!/usr/bin/env python
"""Japanese ASR worker (vocal separation + Qwen3-ASR) - runs in the dedicated
``creator-asr-ja`` conda env.

Standalone: imports ONLY audio_separator / transformers / torch / silero_vad /
soundfile / scipy + stdlib (no creator_agent - that package is not installed
in this env). Invoked as a subprocess by the main env's JaTranscriber.

Usage:
    python ja_worker.py --jobs jobs.json [--model Qwen/Qwen3-ASR-1.7B-hf]
        [--sep-model ""] [--device cuda:0] [--language Japanese] [--keep-vocals]
        [--aligner-model Qwen/Qwen3-ForcedAligner-0.6B]
        [--aligner-python C:/.../creator-asr-ja-aligner/python.exe]
        [--speaker-model iic/speech_campplus_sv_zh-cn_16k-common]

jobs.json = [{"video_id": str, "audio_path": str}, ...]
(audio_path must be 16kHz mono WAV, produced by the caller's extract_audio.)

Pipeline per job: audio-separator vocal isolation -> silero-vad speech
intervals -> [optional: CAM++ speaker embeddings + clustering -> per-speaker
chunks] -> Qwen3-ASR per chunk -> [optional: word-level timestamps via the
aligner_worker.py SUBPROCESS in the separate creator-asr-ja-aligner env]
-> cue splitting at punctuation -> segments. Loads all models ONCE for the
whole batch. Optional steps that fail only degrade themselves (no alignment /
no diarization), never the whole batch.

Word alignment runs out-of-process because qwen-asr (ForcedAligner) pins
transformers==4.57.6 while Qwen3-ASR needs transformers>=5.13 — the two
cannot share an env. --aligner-model + --aligner-python select the subprocess;
without BOTH, cue times fall back to proportional. Prints results wrapped in
sentinel markers (identical contract to asr/worker.py):

    ===ASR_RESULTS_BEGIN===
    [{"video_id":..., "ok":true, "text":..., "segments":[{"start","end","text","speaker"?}],
      "duration_sec":..., "vocals_path":...}, ...]
    ===ASR_RESULTS_END===

Per-video failures set ``ok: false`` with an ``error`` field; they never abort
the batch. Exit code 0 even if some videos failed (the caller reads the JSON).

Model caches: HF models -> ~/.cache/huggingface (set HF_ENDPOINT for mirrors);
separation model -> ~/.cache/audio-separator-models; ModelScope (speaker)
-> ~/.cache/modelscope.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

_RESULT_BEGIN = "===ASR_RESULTS_BEGIN==="
_RESULT_END = "===ASR_RESULTS_END==="

_SAMPLE_RATE = 16000  # silero-vad + Qwen3-ASR both want 16 kHz mono
_MIN_EMBED_SEC = 0.4  # intervals shorter than this give unreliable speaker embeddings
# Extra audio given to the ALIGNER only (not the ASR) around each chunk: VAD
# on the vocal stem often clips soft utterance onsets, and the aligner can
# only place words inside the audio it sees. The wider window lets word times
# recover the true onset/tail; ASR keeps the tight chunk so no text repeats.
_ALIGN_PAD_SEC = 0.8

# Pure-policy modules shared with the main env via this sys.path shim (they
# live in creator_agent, which is not installed here).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cue_splitter import split_cues  # noqa: E402
from speaker_turns import build_speaker_chunks, smooth_labels, speaker_names  # noqa: E402
from vad_chunker import merge_speech_intervals  # noqa: E402


def _emit(results: list) -> None:
    """Print results wrapped in begin/end markers on their own lines."""
    print(_RESULT_BEGIN, flush=True)
    print(json.dumps(results, ensure_ascii=False), flush=True)
    print(_RESULT_END, flush=True)


def _warn(msg: str) -> None:
    print(f"[ja-worker] WARNING: {msg}", file=sys.stderr, flush=True)


def _progress(vid: str, msg: str) -> None:
    """Progress line on stderr — the caller (JaTranscriber) streams stderr
    live to the console, and it's equally readable when the worker is run
    by hand."""
    print(f"[ja-worker][{vid}] {msg}", file=sys.stderr, flush=True)


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


def _aligner_worker_script() -> str:
    """aligner_worker.py ships next to this file (same package dir)."""
    return str(Path(__file__).resolve().parent / "aligner_worker.py")


def _load_speaker(model_id: str):
    """CAM++ speaker-verification pipeline via ModelScope (embeddings)."""
    from modelscope.pipelines import pipeline
    from modelscope.utils.constant import Tasks

    return pipeline(Tasks.speaker_verification, model=model_id)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def _separate_vocals(separator, audio_path: Path, out_dir: Path) -> Path:
    """Isolate the vocal stem; returns the vocals WAV path."""
    # Redirect separator output into the per-job temp dir; otherwise
    # audio-separator writes <name>_(Vocals)_*.wav into the process CWD.
    # The model instance captured output_dir at load_model time, so set both
    # (mirrors the library's own chunked-path re-sync).
    separator.output_dir = str(out_dir)
    if getattr(separator, "model_instance", None) is not None:
        separator.model_instance.output_dir = str(out_dir)
    outputs = separator.separate(str(audio_path))
    # audio-separator returns bare filenames (relative to its output_dir);
    # resolve them against the temp dir we just redirected output into.
    paths = [Path(p) if Path(p).is_absolute() else out_dir / Path(p).name for p in outputs]
    vocals = [p for p in paths if "vocal" in p.stem.lower()]
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


def _speech_intervals(vad_model, vocals_path: Path) -> list[tuple[float, float]]:
    """silero-vad raw speech intervals [(start, end), ...] (unmerged)."""
    import soundfile as sf
    import torch
    from silero_vad import get_speech_timestamps

    data, _ = sf.read(str(vocals_path))
    wav = torch.from_numpy(data).float()
    ts = get_speech_timestamps(
        wav,
        vad_model,
        sampling_rate=_SAMPLE_RATE,
        min_speech_duration_ms=250,
        min_silence_duration_ms=300,
    )
    return [(t["start"] / _SAMPLE_RATE, t["end"] / _SAMPLE_RATE) for t in ts]


def _diarize(
    sv_pipeline,
    vocals_path: Path,
    intervals: list[tuple[float, float]],
    work: Path,
    threshold: float,
    vid: str = "",
) -> list[int]:
    """Cluster per-interval CAM++ embeddings -> speaker label per interval.

    Intervals too short for a stable embedding inherit their neighbour's
    label. Falls back to a single speaker when there is nothing to cluster.
    """
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(vocals_path))
    embs: list = [None] * len(intervals)
    todo = sum(1 for s, e in intervals if e - s >= _MIN_EMBED_SEC)
    done = 0
    for i, (s, e) in enumerate(intervals):
        if e - s < _MIN_EMBED_SEC:
            continue
        clip_path = work / f"spk_{i:04d}.wav"
        sf.write(str(clip_path), data[int(s * sr) : int(e * sr)], sr)
        # modelscope speaker_verification pipeline takes a *list* of clips and,
        # with output_emb=True, returns {'outputs', 'embs'} (embeddings under
        # 'embs', shape [N, 192]) -- NOT 'spk_embedding'.
        out = sv_pipeline([str(clip_path)], output_emb=True)
        embs[i] = np.asarray(out["embs"], dtype=np.float64).ravel()
        done += 1
        if done % 10 == 0 or done == todo:
            _progress(vid, f"  说话人嵌入 {done}/{todo}")

    have = [i for i, e in enumerate(embs) if e is not None]
    labels: list[int | None] = [None] * len(intervals)
    if len(have) > 1:
        mat = np.stack([embs[i] for i in have])
        mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
        from sklearn.cluster import AgglomerativeClustering

        cl = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average", distance_threshold=threshold)
        for i, lab in zip(have, cl.fit_predict(mat)):
            labels[i] = int(lab)
    elif have:
        labels[have[0]] = 0
    # Fill gaps (short intervals) from the previous labelled interval, else the next.
    for i in range(len(labels)):
        if labels[i] is None and i > 0:
            labels[i] = labels[i - 1]
    for i in range(len(labels) - 1, -1, -1):
        if labels[i] is None and i < len(labels) - 1:
            labels[i] = labels[i + 1]
    return [0 if lab is None else lab for lab in labels]


def _transcribe_chunk(processor, model, chunk_wav: Path, language: str) -> str:
    inputs = processor.apply_transcription_request(
        audio=str(chunk_wav),
        language=language,
    ).to(model.device, model.dtype)
    output_ids = model.generate(**inputs, max_new_tokens=512)
    generated = output_ids[:, inputs["input_ids"].shape[1] :]
    return processor.decode(generated, return_format="transcription_only")[0].strip()


_ALIGN_BEGIN = "===ALIGN_RESULTS_BEGIN==="
_ALIGN_END = "===ALIGN_RESULTS_END==="


def _align_batch(
    args, work: Path, asr_chunks: list[dict], vid: str, source_audio: Path, duration: float
) -> dict[int, list]:
    """Word-level timestamps for ALL chunks of one video via the aligner
    subprocess (separate ``creator-asr-ja-aligner`` env). Returns
    ``{chunk_index: [(text, start, end), ...]}``; failed chunks are simply
    absent and the caller falls back to proportional cue times for them.

    Alignment runs on the ORIGINAL mix (``source_audio``), not the vocal
    stem: forced alignment tolerates BGM fine, while vocal separation eats
    soft onsets the aligner would otherwise never see. Each chunk is aligned
    on a window widened by ``_ALIGN_PAD_SEC`` on both sides so word times can
    recover onsets/tails the VAD clipped; ASR text still comes from the
    tight vocal chunk. One subprocess per video -> the aligner model loads
    once per video. Never raises: any config/subprocess/parse failure
    returns {} (proportional).
    """
    if not args.aligner_python:
        _warn("aligner_model set but aligner_python not configured; proportional cue times")
        return {}
    if not Path(args.aligner_python).exists():
        _warn(f"aligner_python not found: {args.aligner_python}; proportional cue times")
        return {}
    try:
        jobs = []
        for c in asr_chunks:
            astart = max(0.0, c["start"] - _ALIGN_PAD_SEC)
            aend = min(duration, c["end"] + _ALIGN_PAD_SEC)
            awav = _slice_wav(source_audio, work / f"align_{c['idx']:04d}.wav", astart, aend)
            jobs.append({"chunk_id": c["idx"], "wav": str(awav), "text": c["text"], "chunk_start": astart})
    except Exception as e:
        _warn(f"failed to slice alignment windows ({e}); proportional cue times")
        return {}
    jobs_path = work / "align_jobs.json"
    jobs_path.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    cmd = [
        args.aligner_python,
        _aligner_worker_script(),
        "--jobs",
        str(jobs_path),
        "--model",
        args.aligner_model,
        "--device",
        args.device,
        "--language",
        args.language,
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    _progress(vid, f"词级对齐：启动独立环境对齐器（{len(jobs)} 块，模型加载约半分钟）…")
    try:
        # Popen + stderr streaming (same pattern as JaTranscriber): aligner
        # progress lines surface live; stdout carries the JSON, drained on a
        # thread to avoid Windows pipe-buffer deadlock.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        stdout_buf: list[bytes] = []

        def _drain() -> None:
            stdout_buf.append(proc.stdout.read() if proc.stdout else b"")

        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line:
                print(line, file=sys.stderr, flush=True)
        rc = proc.wait()
        t.join()
        stdout = (stdout_buf[0] if stdout_buf else b"").decode("utf-8", errors="replace")
        if rc != 0:
            _warn(f"aligner subprocess exited {rc}; proportional cue times")
            return {}
        begin, end = stdout.rfind(_ALIGN_BEGIN), stdout.rfind(_ALIGN_END)
        if begin < 0 or end < 0 or end <= begin:
            _warn(f"aligner output missing markers; proportional cue times. tail: {stdout[-300:]}")
            return {}
        rows = json.loads(stdout[begin + len(_ALIGN_BEGIN) : end].strip())
        out = {row["chunk_id"]: [tuple(w) for w in row["words"]] for row in rows if row.get("ok") and row.get("words")}
        _progress(vid, f"词级对齐完成：{len(out)}/{len(jobs)} 块成功")
        return out
    except Exception as e:
        _warn(f"aligner subprocess failed ({e}); proportional cue times")
        return {}


# ---------------------------------------------------------------------------
# Job driver
# ---------------------------------------------------------------------------


def _process_one(models, job: dict, args, keep_dir: Path | None) -> dict:
    import soundfile as sf

    separator, vad_model, processor, asr_model, sv_pipeline = models
    audio_path = Path(job["audio_path"])
    duration = float(sf.info(str(audio_path)).duration)

    vid = job["video_id"]

    with tempfile.TemporaryDirectory(prefix="ja-asr-") as tmp:
        work = Path(tmp)
        t0 = time.monotonic()
        _progress(vid, "人声分离中…（BS-RoFormer，长视频可能要几分钟）")
        vocals = _separate_vocals(separator, audio_path, work)
        _progress(vid, f"人声分离完成（{time.monotonic() - t0:.0f}s）→ VAD 检测语音段…")

        intervals = _speech_intervals(vad_model, vocals)
        _progress(vid, f"VAD 完成：{len(intervals)} 个语音段")

        # Chunking: per-speaker turns when diarization is on, else plain VAD merge.
        names: dict[int, str] = {}
        if sv_pipeline is not None and intervals:
            try:
                _progress(vid, "说话人分离：提取 CAM++ 嵌入…")
                labels = smooth_labels(_diarize(sv_pipeline, vocals, intervals, work, args.speaker_threshold, vid=vid))
                names = speaker_names(labels)
                _progress(vid, f"说话人聚类完成：{len(names)} 位（{', '.join(names.values())}）")
                raw_chunks = build_speaker_chunks(
                    intervals, labels, max_gap=0.3, max_chunk=args.max_chunk, pad=0.1, duration=duration
                )
                chunks = [(s, e, names.get(lab)) for s, e, lab in raw_chunks]
            except Exception as e:
                _warn(f"diarization failed, falling back to plain chunks: {e}")
                chunks = [(s, e, None) for s, e in _plain_chunks(intervals, args, duration)]
        else:
            chunks = [(s, e, None) for s, e in _plain_chunks(intervals, args, duration)]

        align_on = bool(args.aligner_model and args.aligner_python)
        step = "ASR（随后词级对齐）" if align_on else "ASR"
        _progress(vid, f"分块完成：{len(chunks)} 块 → 开始{step}…")

        # Phase 1: ASR every chunk -> text (on the vocal stem; chunk WAVs stay
        # in `work` only as scratch — phase 2 aligns on the original mix).
        asr_chunks: list[dict] = []
        for i, (start, end, speaker) in enumerate(chunks):
            tc = time.monotonic()
            chunk_wav = _slice_wav(vocals, work / f"chunk_{i:04d}.wav", start, end)
            text = _transcribe_chunk(processor, asr_model, chunk_wav, args.language)
            if not text:
                _progress(vid, f"  块 {i + 1}/{len(chunks)}：无语音内容，跳过")
                continue
            asr_chunks.append(
                {"idx": i, "start": start, "end": end, "speaker": speaker, "wav": chunk_wav, "text": text}
            )
            who = f"[{speaker}] " if speaker else ""
            _progress(
                vid,
                f"  块 {i + 1}/{len(chunks)}（{time.monotonic() - tc:.1f}s）："
                f"{who}{text[:30]}{'…' if len(text) > 30 else ''}",
            )

        # Phase 2: optional word-level alignment — one subprocess for all
        # chunks, aligning on the ORIGINAL mix (window widened by
        # _ALIGN_PAD_SEC) to recover onsets/tails the VAD/separator clipped.
        words_by_idx = (
            _align_batch(args, work, asr_chunks, vid, audio_path, duration) if align_on and asr_chunks else {}
        )

        # Phase 3: split each chunk's text into subtitle cues.
        segments: list[dict] = []
        for c in asr_chunks:
            cues = split_cues(
                c["text"],
                c["start"],
                c["end"],
                words=words_by_idx.get(c["idx"]),
                max_chars=args.max_cue_chars,
                max_sec=args.max_cue_sec,
            )
            for cue in cues:
                seg = {"start": cue["start"], "end": cue["end"], "text": cue["text"]}
                if c["speaker"]:
                    seg["speaker"] = c["speaker"]
                segments.append(seg)

        _progress(vid, f"完成：{len(segments)} 条字幕（共 {time.monotonic() - t0:.0f}s）")

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


def _plain_chunks(intervals: list[tuple[float, float]], args, duration: float) -> list[tuple[float, float]]:
    return merge_speech_intervals(intervals, max_gap=0.3, max_chunk=args.max_chunk, pad=0.1, duration=duration)


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
    parser.add_argument(
        "--aligner-model", default="", help="Qwen3-ForcedAligner HF id / local path (blank = no word alignment)"
    )
    parser.add_argument(
        "--aligner-python",
        default="",
        help="python.exe of the separate creator-asr-ja-aligner env (runs the aligner subprocess)",
    )
    parser.add_argument(
        "--speaker-model", default="", help="ModelScope CAM++ speaker model id (blank = no diarization)"
    )
    parser.add_argument(
        "--speaker-threshold",
        type=float,
        default=0.5,
        help="cosine distance threshold for speaker clustering (lower = more speakers)",
    )
    parser.add_argument("--max-cue-chars", type=int, default=24)
    parser.add_argument("--max-cue-sec", type=float, default=8.0)
    parser.add_argument("--max-chunk", type=float, default=15.0, help="ASR chunk cap in seconds")
    args = parser.parse_args()

    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    if not jobs:
        _emit([])
        return 0

    keep_dir = Path(args.keep_vocals_dir) if args.keep_vocals_dir else None

    _progress("-", f"加载模型中（人声分离 + VAD + {args.model}）…")
    try:
        core = (
            _load_separator(args.sep_model),
            _load_vad(),
            *_load_asr(args.model, args.device),
        )
        _progress("-", "核心模型加载完成")
    except Exception as e:  # core model load failure -> all jobs fail, but we still emit JSON
        traceback.print_exc()
        results = [{"video_id": j["video_id"], "ok": False, "error": f"model load: {e}"} for j in jobs]
        _emit(results)
        return 0

    # Word alignment runs out-of-process (one subprocess per video) — there is
    # no in-env aligner model to load. It only engages when BOTH the model path
    # and the aligner env's python are configured.
    if args.aligner_model and args.aligner_python:
        _progress("-", "词级对齐已启用（独立 aligner 环境子进程，每视频一次）")
    else:
        _progress("-", "未同时配置 aligner_model + aligner_python：字幕时间按比例分摊")
    sv_pipeline = None
    if args.speaker_model:
        try:
            sv_pipeline = _load_speaker(args.speaker_model)
            _progress("-", "说话人模型加载完成（CAM++，首跑会自动下载模型）")
        except Exception as e:
            traceback.print_exc()
            _warn(f"speaker model load failed ({e}); diarization disabled")
    else:
        _progress("-", "未配置 speaker_model：不做说话人分离")

    models = (*core, sv_pipeline)

    results: list[dict] = []
    for job_idx, job in enumerate(jobs):
        vid = job["video_id"]
        _progress(vid, f"===== 视频 {job_idx + 1}/{len(jobs)} =====")
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
