from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from unittest import mock

import pytest

from creator_agent.asr.transcriber import Transcriber
from creator_agent.config import AsrSettings
from creator_agent.models.creator import Creator
from creator_agent.models.video import Video, VideoStatus


def _creator() -> Creator:
    return Creator(
        id="douyin_123",
        platform="douyin",
        platform_uid="123",
        nickname="Tester",
        homepage_url="https://www.douyin.com/user/123",
        added_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _video(vid: str = "v1") -> Video:
    return Video(
        id=vid,
        creator_id="douyin_123",
        platform="douyin",
        platform_vid=vid,
        title="t",
        published_at=datetime(2024, 6, 15, 0, 0, tzinfo=UTC),
        collected_at=datetime(2024, 6, 15, 0, 0, tzinfo=UTC),
        status=VideoStatus.VIDEO_DOWNLOADED,
        storage_path="2024-06-15_Tester",
    )


def _settings() -> AsrSettings:
    return AsrSettings(
        enabled=True,
        env_python=sys.executable,  # exists, so the env check passes
        model="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        device="cpu",
    )


def _worker_stdout(results: list) -> bytes:
    return (
        "some funasr logging\n"
        "===ASR_RESULTS_BEGIN===\n"
        f"{json.dumps(results, ensure_ascii=False)}\n"
        "===ASR_RESULTS_END===\n"
    ).encode("utf-8")


def test_transcribe_batch_empty_returns_zero():
    t = Transcriber(storage=mock.MagicMock(), settings=_settings(), repo=mock.MagicMock())
    assert t.transcribe_batch(_creator(), []) == (0, [])


def test_transcribe_batch_missing_env_python_raises():
    settings = _settings()
    settings.env_python = "/nope/does/not/exist/python.exe"
    t = Transcriber(storage=mock.MagicMock(), settings=settings, repo=mock.MagicMock())
    with pytest.raises(RuntimeError, match="env_python"):
        t.transcribe_batch(_creator(), [_video()])


def test_transcribe_batch_happy_path_writes_files_and_advances_status(tmp_path):
    storage = mock.MagicMock()
    storage.video_file_path.return_value = tmp_path / "video.mp4"
    storage.audio_path.return_value = tmp_path / "audio.wav"
    repo = mock.MagicMock()

    results = [
        {
            "video_id": "v1",
            "ok": True,
            "text": "你好世界",
            "segments": [{"start": 0.0, "end": 1.0, "text": "你好世界"}],
            "duration_sec": 1.0,
        }
    ]
    t = Transcriber(storage=storage, settings=_settings(), repo=repo)
    with (
        mock.patch("creator_agent.asr.transcriber.extract_audio") as extract,
        mock.patch(
            "creator_agent.asr.transcriber.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=_worker_stdout(results), stderr=b""),
        ) as run,
    ):
        transcribed, failed = t.transcribe_batch(_creator(), [_video()])

    assert transcribed == 1
    assert failed == []
    extract.assert_called_once()
    run.assert_called_once()
    # Command invokes the dedicated env python + worker script.
    cmd = run.call_args.args[0]
    assert cmd[0] == sys.executable
    assert "--jobs" in cmd and "--device" in cmd
    # Transcript + txt persisted, status advanced to ASR_DONE.
    storage.save_transcript.assert_called_once()
    storage.save_txt.assert_called_once()
    repo.advance_status.assert_called_once_with("v1", VideoStatus.ASR_DONE)


def test_transcribe_batch_worker_failure_does_not_advance_status():
    storage = mock.MagicMock()
    storage.video_file_path.return_value = "/v.mp4"
    storage.audio_path.return_value = "/a.wav"
    repo = mock.MagicMock()

    results = [{"video_id": "v1", "ok": False, "error": "model load: boom"}]
    t = Transcriber(storage=storage, settings=_settings(), repo=repo)
    with (
        mock.patch("creator_agent.asr.transcriber.extract_audio"),
        mock.patch(
            "creator_agent.asr.transcriber.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=_worker_stdout(results), stderr=b""),
        ),
    ):
        transcribed, failed = t.transcribe_batch(_creator(), [_video()])

    assert transcribed == 0
    assert failed == [("v1", "model load: boom")]
    storage.save_transcript.assert_not_called()
    repo.advance_status.assert_not_called()


def test_transcribe_batch_nonzero_exit_raises():
    t = Transcriber(storage=mock.MagicMock(), settings=_settings(), repo=mock.MagicMock())
    with (
        mock.patch("creator_agent.asr.transcriber.extract_audio"),
        mock.patch(
            "creator_agent.asr.transcriber.subprocess.run",
            return_value=mock.Mock(returncode=2, stdout=b"", stderr=b"traceback"),
        ),
    ):
        with pytest.raises(RuntimeError, match="exited 2"):
            t.transcribe_batch(_creator(), [_video()])


def test_transcribe_batch_audio_extraction_failure_isolated_per_video():
    storage = mock.MagicMock()
    storage.video_file_path.side_effect = ["/v1.mp4", "/v2.mp4"]
    storage.audio_path.side_effect = ["/a1.wav", "/a2.wav"]
    repo = mock.MagicMock()

    def fake_extract(video_path, out_path, ffmpeg_path=""):
        if "v1" in str(video_path):
            raise RuntimeError("ffmpeg broken")
        # v2 extracts fine; worker returns ok for it only.
        return out_path

    results = [{"video_id": "v2", "ok": True, "text": "ok", "segments": [], "duration_sec": None}]
    t = Transcriber(storage=storage, settings=_settings(), repo=repo)
    with (
        mock.patch("creator_agent.asr.transcriber.extract_audio", side_effect=fake_extract),
        mock.patch(
            "creator_agent.asr.transcriber.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=_worker_stdout(results), stderr=b""),
        ),
    ):
        transcribed, failed = t.transcribe_batch(_creator(), [_video("v1"), _video("v2")])

    assert transcribed == 1  # v2 succeeded
    assert len(failed) == 1 and failed[0][0] == "v1"
