"""End-to-end ASR test against the real creator-asr env + GPU.

Marked ``e2e`` (skipped by default; run with ``uv run pytest -m e2e -s``).
Requires:
  - the dedicated ``creator-asr`` conda env with torch (CUDA) + funasr installed,
  - ffmpeg on PATH,
  - at least one video already at ``VIDEO_DOWNLOADED`` in the DB.

Verifies the full path: extract audio -> invoke FunASR worker subprocess ->
write ``transcript.json`` + ``transcript.txt`` -> advance status to ``ASR_DONE``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from creator_agent.asr.transcriber import Transcriber
from creator_agent.config import load_settings
from creator_agent.models.video import VideoStatus
from creator_agent.repository.sqlite_repo import Repository
from creator_agent.storage.file_storage import FileStorage


@pytest.mark.e2e
def test_real_asr_transcribes_one_video():
    settings = load_settings()
    assert settings.asr.env_python, "asr.env_python must be set in config/settings.yaml"
    assert Path(settings.asr.env_python).exists(), f"creator-asr env python missing: {settings.asr.env_python}"

    repo = Repository(settings.db_path)
    storage = FileStorage(settings.storage_dir)
    transcriber = Transcriber(storage=storage, settings=settings.asr, repo=repo)
    try:
        candidates = repo.list_videos_by_status([VideoStatus.VIDEO_DOWNLOADED])
        assert candidates, "No VIDEO_DOWNLOADED videos to transcribe; run a sync first."
        video = candidates[0]
        creator = repo.get_creator(video.creator_id)
        assert creator is not None

        transcribed, failed = transcriber.transcribe_batch(creator, [video])
        assert transcribed == 1, f"ASR failed: {failed}"

        transcript = storage.load_transcript(creator, video)
        assert transcript is not None
        assert transcript.text.strip(), "transcript text is empty"
        assert transcript.segments, "no segments produced"

        txt_path = storage._video_path(creator, video) / "transcript.txt"  # noqa: SLF001
        assert txt_path.exists() and txt_path.read_text(encoding="utf-8").strip()

        refreshed = repo.get_video(video.id)
        assert refreshed.status == VideoStatus.ASR_DONE
    finally:
        repo.close()
