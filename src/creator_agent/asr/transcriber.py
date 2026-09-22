"""ASR orchestrator (runs in the main uv env, no torch dependency).

Extracts audio (ffmpeg), invokes the FunASR worker (dedicated env) as a
subprocess, then writes ``transcript.json`` + ``transcript.txt`` and advances
the video status to ``ASR_DONE``. The model loads once per batch inside the
worker, so prefer :meth:`transcribe_batch` over single-video calls.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from creator_agent.asr.audio_extractor import extract_audio
from creator_agent.models.transcript import Transcript, TranscriptSegment
from creator_agent.models.video import VideoStatus

if TYPE_CHECKING:
    from creator_agent.config import AsrSettings
    from creator_agent.models.creator import Creator
    from creator_agent.repository.sqlite_repo import Repository
    from creator_agent.storage.file_storage import FileStorage

logger = logging.getLogger(__name__)

_RESULT_BEGIN = "===ASR_RESULTS_BEGIN==="
_RESULT_END = "===ASR_RESULTS_END==="


class Transcriber:
    def __init__(
        self,
        storage: FileStorage,
        settings: AsrSettings,
        repo: Repository | None = None,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._repo = repo
        self._worker_script = self._resolve_worker_script()

    def _resolve_worker_script(self) -> str:
        if self._settings.worker_script:
            return self._settings.worker_script
        from creator_agent.asr import worker as _worker

        return str(Path(_worker.__file__))

    def transcribe_batch(
        self,
        creator: Creator,
        videos: list,
    ) -> tuple[int, list[tuple[str, str]]]:
        """Transcribe a batch of videos for one creator.

        Returns ``(transcribed_count, [(video_id, error), ...])``. Per-video
        failures never abort the batch; a video that fails keeps its status
        (``VIDEO_DOWNLOADED``) so the next run retries it.
        """
        if not videos:
            return 0, []

        env_python = self._settings.env_python
        if not env_python or not Path(env_python).exists():
            raise RuntimeError(
                f"asr.env_python not set or missing: {env_python!r}. "
                "Point it at the dedicated creator-asr env's python.exe."
            )

        # 1. Extract audio for each video (idempotent).
        jobs: list[dict] = []
        extracted: list = []  # videos that made it into jobs (for the results loop)
        extract_failed: list[tuple[str, str]] = []
        for video in videos:
            try:
                vpath = self._storage.video_file_path(creator, video)
                apath = self._storage.audio_path(creator, video)
                extract_audio(vpath, apath, self._settings.ffmpeg_path)
                jobs.append({"video_id": video.id, "audio_path": str(apath)})
                extracted.append(video)
            except Exception as e:  # one bad video must not block the rest
                logger.warning("Audio extraction failed for %s: %s", video.id, e)
                extract_failed.append((video.id, str(e)))

        if not jobs:
            return 0, extract_failed

        # 2. Write jobs.json and invoke the worker (model loads once).
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False)
            jobs_path = f.name
        try:
            cmd = [
                env_python,
                self._worker_script,
                "--jobs",
                jobs_path,
                "--model",
                self._settings.model,
                "--vad-model",
                self._settings.vad_model,
                "--punc-model",
                self._settings.punc_model,
                "--device",
                self._settings.device,
            ]
            # Force UTF-8 on the worker so its Chinese stdout/stderr survive the
            # Windows pipe intact, and capture as BYTES then decode with
            # errors="replace" so a stray odd byte can never crash the reader
            # thread (the default text mode uses the system gbk codec on zh CN
            # Windows, which throws UnicodeDecodeError on funasr's output).
            worker_env = os.environ.copy()
            worker_env["PYTHONUTF8"] = "1"
            worker_env["PYTHONIOENCODING"] = "utf-8"
            logger.info("Running ASR worker on %d video(s)...", len(jobs))
            proc = subprocess.run(cmd, capture_output=True, env=worker_env)
            stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
            if proc.returncode != 0:
                raise RuntimeError(f"ASR worker exited {proc.returncode}.\nstderr:\n{stderr[-1500:]}")
            results = self._parse_worker_output(stdout)
        finally:
            Path(jobs_path).unlink(missing_ok=True)

        # 3. Persist transcript + txt, advance status per video.
        transcribed = 0
        failed = list(extract_failed)
        res_by_id = {r.get("video_id"): r for r in results}
        for video in extracted:
            r = res_by_id.get(video.id, {})
            if not r.get("ok"):
                failed.append((video.id, r.get("error") or "no result from worker"))
                logger.warning("ASR failed for %s: %s", video.id, r.get("error"))
                continue
            try:
                segs = [TranscriptSegment(**s) for s in (r.get("segments") or [])]
                transcript = Transcript(
                    video_id=video.id,
                    text=r.get("text", ""),
                    segments=segs,
                    language="zh",
                    model=self._settings.model,
                    duration_sec=r.get("duration_sec"),
                    created_at=datetime.now(UTC),
                )
                self._storage.save_transcript(creator, video, transcript)
                self._storage.save_txt(creator, video, transcript.text)
                if self._repo:
                    self._repo.advance_status(video.id, VideoStatus.ASR_DONE)
                transcribed += 1
                logger.info("ASR done for %s (%d segments).", video.id, len(segs))
            except Exception as e:
                failed.append((video.id, str(e)))
                logger.exception("Failed to persist transcript for %s", video.id)

        return transcribed, failed

    @staticmethod
    def _parse_worker_output(stdout: str) -> list:
        """Extract the JSON payload between the begin/end markers."""
        begin = stdout.rfind(_RESULT_BEGIN)
        end = stdout.rfind(_RESULT_END)
        if begin < 0 or end < 0 or end <= begin:
            raise RuntimeError(f"ASR worker output missing result markers. stdout tail:\n{stdout[-800:]}")
        payload = stdout[begin + len(_RESULT_BEGIN) : end].strip()
        return json.loads(payload)
