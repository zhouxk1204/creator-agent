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
        [--speaker-model iic/speech_campplus_sv_zh-cn_16k-common] [--verbose]

jobs.json = [{"video_id": str, "audio_path": str}, ...]
(audio_path must be 16kHz mono WAV, produced by the caller's extract_audio.)

Pipeline per job (everything downstream of separation runs on the VOCAL stem —
VAD, ASR and the aligner all share one audio source and one timeline):

    vocals.wav -> silero-VAD speech intervals (permissive pass for chunking,
       plus a fixed tight pass for cue auditing / fallback timing)
    -> targeted chunks (~3-8s, hard cap 15s; oversized split at natural pause)
    -> Qwen3-ASR per chunk (+ hallucination repeat-tail cut)
    -> Qwen3-ForcedAligner per chunk (separate aligner-env SUBPROCESS, one per
       video, per-chunk results streamed back)
    -> alignment quality gate per chunk (coverage / window / timing /
       duration) — rejected alignments fall back to SPEECH-AWARE proportional
       cue times (spread over the tight VAD intervals, never over silence)
       WITH a logged reason, never silently
    -> cue splitting at punctuation + long pauses -> drop cues covering no
       speech (hallucinated / misaligned) -> segments
    -> per-video SUMMARY (VAD/chunk/ASR/alignment/cue counters + speech
       ranges left without subtitles)

Optional steps that fail only degrade themselves (no alignment / no
diarization), never the whole batch.

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
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter
from pathlib import Path

_RESULT_BEGIN = "===ASR_RESULTS_BEGIN==="
_RESULT_END = "===ASR_RESULTS_END==="
_CHUNK_MARKER = "===ALIGN_CHUNK=== "

_SAMPLE_RATE = 16000  # silero-vad + Qwen3-ASR both want 16 kHz mono
_MIN_EMBED_SEC = 0.4  # intervals shorter than this give unreliable speaker embeddings
_SHORT_SEG_SEC = 0.5  # VAD segments below this are counted as "short" in the summary

# Pure-policy modules shared with the main env via this sys.path shim (they
# live in creator_agent, which is not installed here).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from align_check import NO_WORDS, AlignCheck, check_alignment  # noqa: E402
from cue_splitter import cut_repeat_tail, resolve_overlaps, split_cues  # noqa: E402
from speaker_turns import build_speaker_chunks, smooth_labels, speaker_names  # noqa: E402
from vad_chunker import build_chunks, speech_coverage, uncovered_ranges  # noqa: E402


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


def _chunk_log(cid: str, stage: str, msg: str) -> None:
    """Per-chunk pipeline line; grep ``CHUNK nnn`` to reconstruct a chunk's
    whole path through VAD -> ASR -> ALIGN -> CUE."""
    print(f"[{cid}][{stage}] {msg}", file=sys.stderr, flush=True)


def _debug(args, cid: str, stage: str, msg: str) -> None:
    """Verbose-only detail (raw VAD segments, word times, cue ranges...)."""
    if getattr(args, "verbose", False):
        _chunk_log(cid, stage, msg)


def _fmt_ts(sec: float) -> str:
    m, s = divmod(max(0.0, sec), 60.0)
    return f"{int(m):02d}:{s:06.3f}"


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


def _separate_vocals(separator, audio_path: Path, out_dir: Path) -> tuple[Path, list[Path]]:
    """Isolate the vocal stem; returns (vocals 16k WAV, [other raw stems]).

    The other stems (e.g. the instrumental) are the separator's raw outputs,
    kept only when the caller passes a keep dir — they let a human verify
    what the separation removed.
    """
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
    return _to_16k_mono(vocals[0], out_dir / "vocals_16k.wav"), [p for p in paths if p != vocals[0]]


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


def _speech_intervals(
    vad_model,
    vocals_path: Path,
    min_speech_ms: int,
    min_silence_ms: int,
    speech_pad_ms: int,
) -> list[tuple[float, float]]:
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
        min_speech_duration_ms=min_speech_ms,
        min_silence_duration_ms=min_silence_ms,
        speech_pad_ms=speech_pad_ms,
    )
    return [(t["start"] / _SAMPLE_RATE, t["end"] / _SAMPLE_RATE) for t in ts]


# Tight VAD pass (fixed params): short silence merge + minimal pad, so the
# intervals hug true utterance edges. Used to AUDIT cues (drop subtitles
# covering no speech) and as the speech-aware proportional fallback timeline
# — NOT for chunking (chunking keeps the permissive configured params, and
# music/noise can fool the permissive pass into long bogus "speech" runs).
_TIGHT_MIN_SPEECH_MS = 100
_TIGHT_MIN_SILENCE_MS = 150
_TIGHT_SPEECH_PAD_MS = 30
# Cues whose tight-VAD speech coverage falls below this are dropped as
# hallucinated / misaligned (logged, counted in the summary).
_MIN_CUE_SPEECH_COV = 0.3


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


def _transcribe_chunk(processor, model, chunk_wav: Path, language: str, max_new_tokens: int) -> str:
    inputs = processor.apply_transcription_request(
        audio=str(chunk_wav),
        language=language,
    ).to(model.device, model.dtype)
    output_ids = model.generate(**inputs, max_new_tokens=max_new_tokens)
    generated = output_ids[:, inputs["input_ids"].shape[1] :]
    return processor.decode(generated, return_format="transcription_only")[0].strip()


def _align_stream(args, work: Path, asr_chunks: list[dict], vid: str, vocals: Path, duration: float) -> dict[int, dict]:
    """Run the aligner subprocess (separate ``creator-asr-ja-aligner`` env)
    over all chunks of one video and stream per-chunk results back.

    Alignment runs on the VOCAL stem (same audio the ASR heard), on a window
    widened by only ``--align-pad`` (default 0.3s) per side — wide enough to
    recover VAD-clipped onsets, narrow enough that the aligner rarely sees a
    neighbouring utterance it could lock repeated words onto.

    Returns ``{chunk_index: {"ok": bool, "words": ...|None, "error"?}}``;
    chunks missing from the dict (subprocess died early) are handled by the
    caller as SUBPROCESS fallbacks. Never raises.
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
            astart = max(0.0, c["start"] - args.align_pad)
            aend = min(duration, c["end"] + args.align_pad)
            awav = _slice_wav(vocals, work / f"align_{c['idx']:04d}.wav", astart, aend)
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
        # stdout carries one ===ALIGN_CHUNK=== line per finished chunk (read
        # here, in order); stderr progress is forwarded live on a thread.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

        def _forward_stderr() -> None:
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    print(line, file=sys.stderr, flush=True)

        t = threading.Thread(target=_forward_stderr, daemon=True)
        t.start()
        results: dict[int, dict] = {}
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith(_CHUNK_MARKER):
                continue
            try:
                rec = json.loads(line[len(_CHUNK_MARKER) :])
                results[int(rec["chunk_id"])] = rec
            except (ValueError, KeyError) as e:
                _warn(f"unparseable aligner chunk line ({e}); some chunks may fall back")
        rc = proc.wait()
        t.join()
        if rc != 0:
            _warn(f"aligner subprocess exited {rc}; {len(asr_chunks) - len(results)} chunk(s) missing -> proportional")
        _progress(vid, f"词级对齐完成：{len(results)}/{len(jobs)} 块返回")
        return results
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
    stats: Counter = Counter()

    with tempfile.TemporaryDirectory(prefix="ja-asr-") as tmp:
        work = Path(tmp)
        t0 = time.monotonic()
        _progress(vid, "人声分离中…（BS-RoFormer，长视频可能要几分钟）")
        vocals, other_stems = _separate_vocals(separator, audio_path, work)
        _progress(vid, f"人声分离完成（{time.monotonic() - t0:.0f}s）→ VAD 检测语音段…")

        # Chunking pass uses the configured (permissive) params: low
        # min_speech keeps 「あっ」「えっ」-type interjections, speech_pad
        # keeps soft onsets from being clipped. The tight pass hugs true
        # utterance edges for cue auditing + fallback timing.
        intervals = _speech_intervals(
            vad_model, vocals, args.vad_min_speech_ms, args.vad_min_silence_ms, args.vad_speech_pad_ms
        )
        tight = _speech_intervals(vad_model, vocals, _TIGHT_MIN_SPEECH_MS, _TIGHT_MIN_SILENCE_MS, _TIGHT_SPEECH_PAD_MS)
        stats["vad_segments"] = len(intervals)
        stats["vad_short"] = sum(1 for s, e in intervals if e - s < _SHORT_SEG_SEC)
        stats["vad_long"] = sum(1 for s, e in intervals if e - s > args.max_chunk)
        _progress(vid, f"VAD 完成：{len(intervals)} 个语音段（短 {_SHORT_SEG_SEC}s 内 {stats['vad_short']} 个）")
        _debug(args, "VAD", "RAW", ", ".join(f"{s:.2f}-{e:.2f}" for s, e in intervals))

        # Chunking: per-speaker turns when diarization is on, else the
        # targeted 3-8s chunker with a hard 15s cap.
        names: dict[int, str] = {}
        chunks: list[dict] = []
        if sv_pipeline is not None and intervals:
            try:
                _progress(vid, "说话人分离：提取 CAM++ 嵌入…")
                labels = smooth_labels(_diarize(sv_pipeline, vocals, intervals, work, args.speaker_threshold, vid=vid))
                names = speaker_names(labels)
                _progress(vid, f"说话人聚类完成：{len(names)} 位（{', '.join(names.values())}）")
                raw_chunks = build_speaker_chunks(
                    intervals, labels, max_gap=0.3, max_chunk=args.max_chunk, pad=0.1, duration=duration
                )
                chunks = [
                    {"start": s, "end": e, "reason": "speaker_turn", "segments": 0, "speaker": names.get(lab)}
                    for s, e, lab in raw_chunks
                ]
            except Exception as e:
                _warn(f"diarization failed, falling back to plain chunks: {e}")
                chunks = []
        if not chunks:
            chunks = build_chunks(
                intervals,
                target_min=args.chunk_target_min,
                target_max=args.chunk_target_max,
                hard_max=args.max_chunk,
                pad=0.1,
                duration=duration,
            )
        for i, c in enumerate(chunks):
            c["idx"] = i
            c.setdefault("speaker", None)
            stats[f"chunk_{c['reason']}"] += 1
            _debug(
                args,
                f"CHUNK {i + 1:03d}/{len(chunks):03d}",
                "CHUNKER",
                f"{c['start']:.2f}-{c['end']:.2f} ({c['end'] - c['start']:.2f}s) "
                f"reason={c['reason']} source_segments={c['segments']}",
            )

        align_on = bool(args.aligner_model and args.aligner_python)
        step = "ASR（随后词级对齐）" if align_on else "ASR"
        _progress(vid, f"分块完成：{len(chunks)} 块 → 开始{step}…")

        # Phase 1: ASR every chunk on the vocal stem.
        asr_chunks: list[dict] = []
        for c in chunks:
            cid = f"CHUNK {c['idx'] + 1:03d}/{len(chunks):03d}"
            tc = time.monotonic()
            try:
                chunk_wav = _slice_wav(vocals, work / f"chunk_{c['idx']:04d}.wav", c["start"], c["end"])
                text = _transcribe_chunk(processor, asr_model, chunk_wav, args.language, args.max_new_tokens)
            except Exception as e:
                stats["asr_failed"] += 1
                _chunk_log(cid, "ASR", f"FAIL reason=EXCEPTION error={e}")
                continue
            el = time.monotonic() - tc
            if not text:
                stats["asr_empty"] += 1
                _chunk_log(cid, "ASR", f"EMPTY reason=NO_TEXT ({el:.1f}s)")
                continue
            # Hallucination guard: on music/noise the model loops one unit
            # (「バイバイ。バイバイ。…」); cut the degenerate tail BEFORE
            # alignment so phantom words can't drag real cues off their audio.
            cut = cut_repeat_tail(text)
            stripped = "".join(text.split())
            if len(cut) < len(stripped):
                stats["asr_repeat_cut"] += 1
                _chunk_log(cid, "ASR", f'repeat-tail cut {len(stripped)}->{len(cut)} chars tail="{stripped[len(cut) :][:20]}…"')
                text = cut
            stats["asr_ok"] += 1
            preview = text[:30] + ("…" if len(text) > 30 else "")
            _chunk_log(cid, "ASR", f'OK {el:.1f}s len={len(text)} text="{preview}"')
            asr_chunks.append({**c, "wav": chunk_wav, "text": text})

        # Phase 2+3, pipelined per chunk: align (one subprocess, results
        # streamed per chunk) -> quality gate -> cue split. Rejected
        # alignments fall back to proportional cue times with a logged reason.
        align_results = _align_stream(args, work, asr_chunks, vid, vocals, duration) if align_on else {}

        segments: list[dict] = []
        for c in asr_chunks:
            cid = f"CHUNK {c['idx'] + 1:03d}/{len(chunks):03d}"
            words = None
            if align_on:
                check = _gate_alignment(align_results.get(c["idx"]), c, args)
                if check.ok:
                    words = [tuple(w) for w in align_results[c["idx"]]["words"]]
                    stats["align_ok"] += 1
                    ws, we = words[0][1], words[-1][2]
                    _chunk_log(
                        cid,
                        "ALIGN",
                        f"OK words={len(words)} coverage={check.coverage:.0%} timing={ws:.2f}-{we:.2f}",
                    )
                    _debug(
                        args,
                        cid,
                        "ALIGN",
                        "words: " + ", ".join(f'"{w[0]}" {w[1]:.2f}-{w[2]:.2f}' for w in words),
                    )
                else:
                    stats[f"align_fallback_{check.reason}"] += 1
                    detail = f" detail={check.detail}" if check.detail else ""
                    _chunk_log(cid, "ALIGN", f"FAIL reason={check.reason}{detail} fallback=PROPORTIONAL")
            else:
                stats["align_fallback_DISABLED"] += 1

            cues = split_cues(
                c["text"],
                c["start"],
                c["end"],
                words=words,
                max_chars=args.max_cue_chars,
                max_sec=args.max_cue_sec,
                pause_sec=args.pause_sec,
                # Speech-aware fallback: when word alignment is rejected, cues
                # are spread over the tight speech intervals only (never over
                # silence), with hard breaks at inter-utterance gaps.
                speech=[(s, e) for s, e in tight if e > c["start"] and s < c["end"]],
            )
            # Drop cues covering (almost) no speech under the tight VAD pass:
            # hallucinated text on music/silence, or words the aligner locked
            # onto the wrong utterance. Real dialogue is essentially never
            # below this floor, and every drop is logged + counted.
            kept = []
            for cue in cues:
                cov = speech_coverage(cue["start"], cue["end"], tight)
                if cov < _MIN_CUE_SPEECH_COV:
                    stats["cues_dropped_no_speech"] += 1
                    _chunk_log(
                        cid,
                        "CUE",
                        f'DROP speech_cov={cov:.0%} {_fmt_ts(cue["start"])}-{_fmt_ts(cue["end"])} "{cue["text"][:20]}"',
                    )
                    continue
                kept.append(cue)
            cues = kept
            stats["cues"] += len(cues)
            timed = "word-timed" if words else "proportional"
            _chunk_log(cid, "CUE", f"generated={len(cues)} ({timed})")
            for cue in cues:
                _debug(args, cid, "CUE", f"  {_fmt_ts(cue['start'])} -> {_fmt_ts(cue['end'])} {cue['text']}")
                seg = {"start": cue["start"], "end": cue["end"], "text": cue["text"]}
                if c["speaker"]:
                    seg["speaker"] = c["speaker"]
                segments.append(seg)

        # Word-timed cues may spill slightly past their chunk (the align
        # window is padded beyond it) — resolve tiny residual cross-chunk
        # overlaps. This is LAST-RESORT insurance only; the quality gate
        # above rejects the badly-shifted alignments it used to "repair".
        segments = resolve_overlaps(segments)

        _print_summary(vid, duration, stats, chunks, time.monotonic() - t0, intervals, segments)

        vocals_out = None
        if keep_dir is not None:
            keep_dir.mkdir(parents=True, exist_ok=True)
            vocals_out = keep_dir / f"{job['video_id']}.vocals.wav"
            vocals_out.write_bytes(vocals.read_bytes())
            # Keep the non-vocal stems too (e.g. the instrumental) so the
            # separation quality can be audited by ear afterwards.
            for stem in other_stems:
                kind = "instrumental" if "instrument" in stem.stem.lower() else "other"
                shutil.copy2(stem, keep_dir / f"{job['video_id']}.{kind}{stem.suffix}")

    return {
        "text": "".join(s["text"] for s in segments),
        "segments": segments,
        "duration_sec": round(duration, 3),
        "vocals_path": str(vocals_out) if vocals_out else None,
    }


def _gate_alignment(rec: dict | None, c: dict, args) -> AlignCheck:
    """Quality-gate one chunk's aligner result (see align_check for the five
    checks). ``rec`` None means the subprocess never returned this chunk."""
    if rec is None:
        return AlignCheck(False, "SUBPROCESS", detail="aligner subprocess exited before returning this chunk")
    if rec.get("error"):
        return AlignCheck(False, "EXCEPTION", detail=str(rec["error"])[:200])
    if not rec.get("ok") or not rec.get("words"):
        return AlignCheck(False, NO_WORDS)
    return check_alignment(
        [tuple(w) for w in rec["words"]],
        c["text"],
        c["start"],
        c["end"],
        pad=args.align_pad,
        min_coverage=args.align_min_coverage,
    )


def _print_summary(
    vid: str,
    duration: float,
    stats: Counter,
    chunks: list[dict],
    elapsed: float,
    intervals: list[tuple[float, float]],
    segments: list[dict],
) -> None:
    """Per-video pipeline summary — the counters that tell you WHERE a bad
    episode went wrong (VAD? ASR? alignment? which fallback reason?), plus a
    list of spoken ranges that ended up with no subtitle at all."""
    chunk_reasons = sorted(k for k in stats if k.startswith("chunk_"))
    fallback_reasons = sorted(k for k in stats if k.startswith("align_fallback_"))
    lines = [
        "=" * 60,
        f"[{vid}] ASR SUMMARY",
        "=" * 60,
        "Audio",
        f"  duration              : {_fmt_ts(duration)}",
        "VAD",
        f"  segments              : {stats['vad_segments']}",
        f"  short (<{_SHORT_SEG_SEC}s)         : {stats['vad_short']}",
        f"  long (>hard_max)        : {stats['vad_long']}",
        "Chunks",
        f"  total                 : {len(chunks)}",
    ]
    lines += [f"  {k.removeprefix('chunk_'):<21}: {stats[k]}" for k in chunk_reasons]
    lines += [
        "ASR",
        f"  success               : {stats['asr_ok']}",
        f"  empty                 : {stats['asr_empty']}",
        f"  failed                : {stats['asr_failed']}",
        f"  repeat-tail cut       : {stats['asr_repeat_cut']}",
        "Alignment",
        f"  success               : {stats['align_ok']}",
        f"  fallback              : {sum(stats[k] for k in fallback_reasons)}",
    ]
    lines += [f"    {k.removeprefix('align_fallback_'):<19}: {stats[k]}" for k in fallback_reasons]
    lines += [
        "Subtitle",
        f"  cues                  : {stats['cues']}",
        f"  dropped (no speech)   : {stats['cues_dropped_no_speech']}",
    ]
    # Speech the subtitles never covered (ASR dropped it, or its cues were
    # rejected/filtered away). Long ranges here = missing dialogue.
    missed = uncovered_ranges(intervals, [(s["start"], s["end"]) for s in segments], min_sec=1.0)
    missed_total = sum(e - s for s, e in missed)
    lines += [
        "Coverage",
        f"  speech not subtitled  : {missed_total:.1f}s in {len(missed)} range(s)",
    ]
    for s, e in missed[:12]:
        lines.append(f"    {_fmt_ts(s)} - {_fmt_ts(e)} ({e - s:.1f}s)")
    if len(missed) > 12:
        lines.append(f"    ... and {len(missed) - 12} more")
    lines += [
        f"Elapsed                 : {elapsed:.1f}s",
        "=" * 60,
    ]
    for ln in lines:
        print(ln, file=sys.stderr, flush=True)


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
    parser.add_argument(
        "--pause-sec",
        type=float,
        default=0.6,
        help="break subtitle cues at silence gaps of at least this length (needs word alignment)",
    )
    # VAD (defaults catch short interjections and keep soft onsets).
    parser.add_argument("--vad-min-speech-ms", type=int, default=120)
    parser.add_argument("--vad-min-silence-ms", type=int, default=300)
    parser.add_argument("--vad-speech-pad-ms", type=int, default=150)
    # Chunking: aim for 3-8s chunks, never exceed --max-chunk.
    parser.add_argument("--chunk-target-min", type=float, default=3.0)
    parser.add_argument("--chunk-target-max", type=float, default=8.0)
    parser.add_argument("--max-chunk", type=float, default=15.0, help="hard ASR chunk cap in seconds")
    parser.add_argument("--max-new-tokens", type=int, default=1024, help="ASR generation cap")
    # Alignment: window padding and the coverage floor of the quality gate.
    parser.add_argument("--align-pad", type=float, default=0.3, help="alignment window padding per side (s)")
    parser.add_argument("--align-min-coverage", type=float, default=0.5, help="min word coverage to trust alignment")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging (VAD segments, word times, cues)")
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
