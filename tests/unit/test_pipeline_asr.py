from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import pytest

from creator_agent.collector.base import today_filter
from creator_agent.models.creator import Creator
from creator_agent.models.video import Video, VideoStatus
from creator_agent.pipeline.runner import PipelineRunner


def _creator() -> Creator:
    return Creator(
        id="douyin_123",
        platform="douyin",
        platform_uid="123",
        nickname="Tester",
        homepage_url="https://www.douyin.com/user/123",
        added_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _video(vid: str, creator_id: str = "douyin_123", status=VideoStatus.VIDEO_DOWNLOADED) -> Video:
    return Video(
        id=vid,
        creator_id=creator_id,
        platform="douyin",
        platform_vid=vid,
        title="t",
        published_at=datetime(2024, 6, 15, 0, 0, tzinfo=UTC),
        collected_at=datetime(2024, 6, 15, 0, 0, tzinfo=UTC),
        status=status,
        storage_path=f"2024-06-15_Tester_{vid}",
    )


def _runner(transcriber=None):
    runner = PipelineRunner(
        repo=mock.MagicMock(),
        storage=mock.MagicMock(),
        browser=mock.MagicMock(),
        downloader=mock.MagicMock(),
        transcriber=transcriber,
    )
    runner._collector = mock.MagicMock()
    return runner


def test_asr_candidates_filters_by_creator_and_transcript():
    runner = _runner()
    creator = _creator()
    v_pending = _video("v1")  # creator match, no transcript -> keep
    v_done = _video("v2")  # creator match, has transcript -> drop
    v_other = _video("v3", creator_id="douyin_other")  # other creator -> drop

    runner._repo.list_videos_by_status.return_value = [v_pending, v_done, v_other]
    # load_transcript returns None for v_pending, a truthy object for v_done.
    runner._storage.load_transcript.side_effect = lambda c, v: None if v.id == "v1" else mock.sentinel.T

    candidates = runner._asr_candidates(creator)
    assert [c.id for c in candidates] == ["v1"]


def test_asr_candidates_respects_limit():
    runner = _runner()
    creator = _creator()
    runner._repo.list_videos_by_status.return_value = [_video(f"v{i}") for i in range(5)]
    runner._storage.load_transcript.return_value = None
    assert len(runner._asr_candidates(creator, limit=2)) == 2


def test_run_asr_delegates_to_transcriber():
    transcriber = mock.MagicMock()
    transcriber.transcribe_batch.return_value = (2, [("v3", "oops")])
    runner = _runner(transcriber=transcriber)
    creator = _creator()
    runner._repo.list_videos_by_status.return_value = [_video("v1"), _video("v2")]
    runner._storage.load_transcript.return_value = None

    result = runner.run_asr(creator, limit=5)
    assert result.transcribed == 2
    assert result.failed == [("v3", "oops")]
    transcriber.transcribe_batch.assert_called_once()
    args, _ = transcriber.transcribe_batch.call_args
    assert args[0] == creator
    assert [v.id for v in args[1]] == ["v1", "v2"]


def test_run_asr_no_candidates_returns_empty():
    transcriber = mock.MagicMock()
    runner = _runner(transcriber=transcriber)
    runner._repo.list_videos_by_status.return_value = []
    result = runner.run_asr(_creator())
    assert result.transcribed == 0
    transcriber.transcribe_batch.assert_not_called()


def test_run_asr_raises_without_transcriber():
    runner = _runner(transcriber=None)
    with pytest.raises(RuntimeError, match="ASR is not configured"):
        runner.run_asr(_creator())


def test_sync_creator_do_asr_transcribes_downloaded_videos():
    transcriber = mock.MagicMock()
    transcriber.transcribe_batch.return_value = (1, [])
    runner = _runner(transcriber=transcriber)
    creator = _creator()

    # Collector returns one collected video; repo upsert says it's already downloaded.
    collected = [mock.MagicMock()]
    runner._collector.collect.return_value = collected
    the_video = _video("v1", status=VideoStatus.VIDEO_DOWNLOADED)
    runner._repo.upsert_collected.return_value = the_video
    runner._repo.start_sync.return_value = 42
    # ASR candidate selection: the downloaded video is pending (no transcript yet).
    runner._repo.list_videos_by_status.return_value = [the_video]
    runner._storage.load_transcript.return_value = None

    result = runner.sync_creator(creator, today_filter(), do_asr=True)
    assert result.transcribed == 1
    transcriber.transcribe_batch.assert_called_once()


def test_sync_creator_without_do_asr_skips_asr():
    transcriber = mock.MagicMock()
    runner = _runner(transcriber=transcriber)
    runner._collector.collect.return_value = []
    runner._repo.start_sync.return_value = 1

    result = runner.sync_creator(_creator(), today_filter(), do_asr=False)
    assert result.transcribed == 0
    transcriber.transcribe_batch.assert_not_called()


def test_sync_creator_do_asr_without_transcriber_warns_not_crashes():
    runner = _runner(transcriber=None)
    runner._collector.collect.return_value = []
    runner._repo.start_sync.return_value = 1
    # Should complete (partial/ok) rather than raise.
    result = runner.sync_creator(_creator(), today_filter(), do_asr=True)
    assert result.transcribed == 0
