"""Japanese ASR orchestrator (runs in the main uv env, no torch dependency).

Standalone file-based tool, NOT wired into the sync pipeline: takes local
video files, extracts audio (ffmpeg), invokes the ja worker (dedicated
``creator-asr-ja`` env) as a subprocess — vocal separation + silero-vad
chunking + Qwen3-ASR — then writes ``<name>.transcript.json`` / ``<name>.txt``
/ ``<name>.srt`` next to each video (or into ``--out-dir``). The worker loads
all models once per batch, so pass all files in one call.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from creator_agent.asr.audio_extractor import extract_audio
from creator_agent.asr.subtitle import write_srt
from creator_agent.models.transcript import Transcript, TranscriptSegment

if TYPE_CHECKING:
    from creator_agent.config import JaAsrSettings

logger = logging.getLogger(__name__)

_RESULT_BEGIN = "===ASR_RESULTS_BEGIN==="
_RESULT_END = "===ASR_RESULTS_END==="

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".ts", ".m2ts", ".flv", ".avi"}


def _progress(msg: str) -> None:
    """User-visible progress on stderr (works from the CLI, the bat, and
    scripts); stdlib logging stays for the log file."""
    print(f"[ja-asr] {msg}", file=sys.stderr, flush=True)
    logger.info(msg)


def scan_videos(input_dir: Path) -> list[Path]:
    """All video files under ``input_dir`` (recursive), sorted by name."""
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        return []
    return sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


def split_pending(paths: list[Path], out_dir: Path | None = None) -> tuple[list[Path], list[Path]]:
    """Split ``paths`` into (pending, already-done). A video counts as done
    when ``<stem>.srt`` exists in its target dir (out_dir, else next to it)."""
    pending, done = [], []
    for p in paths:
        target = (Path(out_dir) if out_dir else p.parent) / f"{p.stem}.srt"
        (done if target.exists() else pending).append(p)
    return pending, done


class JaTranscriber:
    def __init__(self, settings: JaAsrSettings) -> None:
        self._settings = settings
        from creator_agent.asr import ja_worker as _worker

        self._worker_script = str(Path(_worker.__file__))

    def transcribe_files(
        self,
        video_paths: list[Path],
        out_dir: Path | None = None,
    ) -> tuple[int, list[tuple[str, str]]]:
        """Transcribe local video files with vocal separation + Qwen3-ASR.

        Returns ``(transcribed_count, [(name, error), ...])``. Per-file
        failures never abort the batch. Outputs land in ``out_dir`` or next to
        each source video.
        """
        if not video_paths:
            return 0, []

        env_python = self._settings.env_python
        if not env_python or not Path(env_python).exists():
            raise RuntimeError(
                f"ja_asr.env_python not set or missing: {env_python!r}. "
                "Point it at the dedicated creator-asr-ja env's python."
            )

        # 1. Extract audio for each video into a temp dir.
        failed: list[tuple[str, str]] = []
        jobs: list[dict] = []
        extracted: list[tuple[Path, str]] = []  # (video_path, job video_id)
        with tempfile.TemporaryDirectory(prefix="ja-asr-") as tmp:
            audio_dir = Path(tmp)
            used_ids: set[str] = set()
            for idx, vpath in enumerate(video_paths):
                vpath = Path(vpath)
                vid = self._unique_id(vpath.stem, used_ids)
                _progress(f"提取音频 {idx + 1}/{len(video_paths)}：{vpath.name}")
                try:
                    apath = audio_dir / f"{vid}.wav"
                    extract_audio(vpath, apath, self._settings.ffmpeg_path)
                    jobs.append({"video_id": vid, "audio_path": str(apath)})
                    extracted.append((vpath, vid))
                except Exception as e:  # one bad video must not block the rest
                    logger.warning("Audio extraction failed for %s: %s", vpath, e)
                    _progress(f"  ! 提取失败：{vpath.name}: {e}")
                    failed.append((vpath.name, str(e)))

            if not jobs:
                return 0, failed

            # 2. Invoke the worker (models load once for the whole batch).
            results = self._run_worker(jobs, out_dir)

        # 3. Write transcript.json + txt + srt per video.
        transcribed = 0
        res_by_id = {r.get("video_id"): r for r in results}
        for vpath, vid in extracted:
            r = res_by_id.get(vid, {})
            if not r.get("ok"):
                failed.append((vpath.name, r.get("error") or "no result from worker"))
                logger.warning("JA ASR failed for %s: %s", vpath, r.get("error"))
                continue
            try:
                self._write_outputs(vpath, out_dir, r)
                transcribed += 1
                logger.info("JA ASR done for %s (%d segments).", vpath.name, len(r.get("segments") or []))
            except Exception as e:
                failed.append((vpath.name, str(e)))
                logger.exception("Failed to persist transcript for %s", vpath)

        return transcribed, failed

    # ------------------------------------------------------------------

    def _run_worker(self, jobs: list[dict], out_dir: Path | None) -> list:
        import threading

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False)
            jobs_path = f.name
        try:
            s = self._settings
            cmd = [
                s.env_python,
                self._worker_script,
                "--jobs",
                jobs_path,
                "--model",
                s.model,
                "--sep-model",
                s.sep_model,
                "--device",
                s.device,
                "--language",
                s.language,
                "--speaker-threshold",
                str(s.speaker_threshold),
                "--max-cue-chars",
                str(s.max_cue_chars),
                "--max-cue-sec",
                str(s.max_cue_sec),
                "--max-chunk",
                str(s.max_chunk_sec),
            ]
            if s.aligner_model:
                cmd += ["--aligner-model", s.aligner_model]
            if s.speaker_model:
                cmd += ["--speaker-model", s.speaker_model]
            if self._settings.keep_vocals and out_dir:
                cmd += ["--keep-vocals-dir", str(out_dir)]
            # Same rationale as Transcriber: force UTF-8 in the worker and
            # decode bytes with errors="replace" (Windows gbk pipe safety).
            worker_env = os.environ.copy()
            worker_env["PYTHONUTF8"] = "1"
            worker_env["PYTHONIOENCODING"] = "utf-8"
            # audio-separator shells out to `ffmpeg` (plain PATH lookup), but
            # our ffmpeg is a full-build path from config, not on the system
            # PATH — expose its dir to the worker subprocess.
            ffmpeg_dir = str(Path(self._settings.ffmpeg_path).parent)
            worker_env["PATH"] = ffmpeg_dir + os.pathsep + worker_env.get("PATH", "")
            logger.info("Running JA ASR worker on %d video(s)...", len(jobs))
            _progress(f"启动 ASR worker（{len(jobs)} 个视频，模型加载约需 1~2 分钟）…")
            # Popen instead of run(): the worker prints one progress line per
            # step (separation / VAD / diarization / per-chunk ASR) on stderr —
            # stream those live so long batches are watchable; stdout carries
            # the result JSON and is drained on a thread (Windows pipe buffers
            # are tiny, reading both from one thread would deadlock).
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=worker_env)
            stdout_buf: list[bytes] = []
            stderr_tail: list[str] = []

            def _drain_stdout() -> None:
                stdout_buf.append(proc.stdout.read() if proc.stdout else b"")

            t = threading.Thread(target=_drain_stdout, daemon=True)
            t.start()
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    print(line, file=sys.stderr, flush=True)
                    stderr_tail.append(line)
                    del stderr_tail[:-50]
            rc = proc.wait()
            t.join()
            stdout = (stdout_buf[0] if stdout_buf else b"").decode("utf-8", errors="replace")
            if rc != 0:
                raise RuntimeError(f"JA ASR worker exited {rc}.\nstderr:\n" + "\n".join(stderr_tail[-25:]))
            return self._parse_worker_output(stdout)
        finally:
            Path(jobs_path).unlink(missing_ok=True)

    def _write_outputs(self, vpath: Path, out_dir: Path | None, r: dict) -> None:
        target_dir = Path(out_dir) if out_dir else vpath.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = vpath.stem

        segs = [TranscriptSegment(**s) for s in (r.get("segments") or [])]
        transcript = Transcript(
            video_id=stem,
            text=r.get("text", ""),
            segments=segs,
            language="ja",
            model=self._settings.model,
            duration_sec=r.get("duration_sec"),
            created_at=datetime.now(UTC),
        )
        (target_dir / f"{stem}.transcript.json").write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
        # Segmented text: one segment per line for easy reading / diffing.
        lines = [f"{s.speaker}: {s.text}" if s.speaker else s.text for s in segs if s.text.strip()]
        (target_dir / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        write_srt(segs, target_dir / f"{stem}.srt")

    @staticmethod
    def _unique_id(stem: str, used: set[str]) -> str:
        """Disambiguate duplicate file stems from different directories."""
        vid, i = stem, 2
        while vid in used:
            vid = f"{stem}_{i}"
            i += 1
        used.add(vid)
        return vid

    @staticmethod
    def _parse_worker_output(stdout: str) -> list:
        """Extract the JSON payload between the begin/end markers."""
        begin = stdout.rfind(_RESULT_BEGIN)
        end = stdout.rfind(_RESULT_END)
        if begin < 0 or end < 0 or end <= begin:
            raise RuntimeError(f"JA ASR worker output missing result markers. stdout tail:\n{stdout[-800:]}")
        payload = stdout[begin + len(_RESULT_BEGIN) : end].strip()
        return json.loads(payload)
