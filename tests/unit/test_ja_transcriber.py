from __future__ import annotations

import io
import json
import sys
from unittest import mock

import pytest

from creator_agent.asr.ja_transcriber import JaTranscriber
from creator_agent.config import JaAsrSettings


def _settings() -> JaAsrSettings:
    return JaAsrSettings(
        env_python=sys.executable,  # exists, so the env check passes
        model="Qwen/Qwen3-ASR-1.7B-hf",
        device="cpu",
        language="Japanese",
    )


def _worker_stdout(results: list) -> bytes:
    return (
        "some worker logging\n"
        "===ASR_RESULTS_BEGIN===\n"
        f"{json.dumps(results, ensure_ascii=False)}\n"
        "===ASR_RESULTS_END===\n"
    ).encode()


def _popen_patch(results: list | None = None, rc: int = 0, stderr: bytes = b"") -> mock.Mock:
    """Patch subprocess.Popen with a fake worker process (real BytesIO pipes,
    since _run_worker streams stderr and drains stdout on a thread)."""
    proc = mock.Mock()
    proc.stdout = io.BytesIO(_worker_stdout(results or []))
    proc.stderr = io.BytesIO(stderr)
    proc.wait.return_value = rc
    return mock.patch("creator_agent.asr.ja_transcriber.subprocess.Popen", return_value=proc)


def _ok_result(vid: str, text: str = "こんにちは世界") -> dict:
    return {
        "video_id": vid,
        "ok": True,
        "text": text,
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "こんにちは"},
            {"start": 2.0, "end": 3.0, "text": "世界"},
        ],
        "duration_sec": 3.0,
        "vocals_path": None,
    }


def test_transcribe_files_empty_returns_zero():
    t = JaTranscriber(settings=_settings())
    assert t.transcribe_files([]) == (0, [])


def test_transcribe_files_missing_env_python_raises(tmp_path):
    settings = _settings()
    settings.env_python = "/nope/does/not/exist/python"
    t = JaTranscriber(settings=settings)
    with pytest.raises(RuntimeError, match="env_python"):
        t.transcribe_files([tmp_path / "a.mp4"])


def test_transcribe_files_happy_path_writes_three_outputs(tmp_path):
    video = tmp_path / "ep01.mp4"
    video.write_bytes(b"fake")
    results = [_ok_result("ep01")]

    t = JaTranscriber(settings=_settings())
    with (
        mock.patch("creator_agent.asr.ja_transcriber.extract_audio") as extract,
        _popen_patch(results) as popen,
    ):
        done, failed = t.transcribe_files([video])

    assert done == 1 and failed == []
    extract.assert_called_once()
    cmd = popen.call_args.args[0]
    assert cmd[0] == sys.executable
    assert "--jobs" in cmd and "--language" in cmd and "Japanese" in cmd

    transcript = json.loads((tmp_path / "ep01.transcript.json").read_text(encoding="utf-8"))
    assert transcript["text"] == "こんにちは世界"
    assert transcript["language"] == "ja"
    assert len(transcript["segments"]) == 2

    txt = (tmp_path / "ep01.txt").read_text(encoding="utf-8")
    assert txt == "こんにちは\n世界\n"

    srt = (tmp_path / "ep01.srt").read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> 00:00:01,500\nこんにちは" in srt
    assert "2\n00:00:02,000 --> 00:00:03,000\n世界" in srt


def test_transcribe_files_out_dir_overrides_output_location(tmp_path):
    video = tmp_path / "ep01.mp4"
    video.write_bytes(b"fake")
    out_dir = tmp_path / "out"
    results = [_ok_result("ep01")]

    t = JaTranscriber(settings=_settings())
    with (
        mock.patch("creator_agent.asr.ja_transcriber.extract_audio"),
        _popen_patch(results),
    ):
        done, failed = t.transcribe_files([video], out_dir=out_dir)

    assert done == 1 and failed == []
    # Outputs land in a per-video subdirectory of out_dir.
    assert (out_dir / "ep01" / "ep01.srt").exists()
    assert not (tmp_path / "ep01.srt").exists()


def test_transcribe_files_work_dir_keeps_intermediates(tmp_path):
    video = tmp_path / "ep01.mp4"
    video.write_bytes(b"fake")
    work = tmp_path / "work"
    settings = _settings()
    settings.work_dir = str(work)

    t = JaTranscriber(settings=settings)
    with (
        mock.patch("creator_agent.asr.ja_transcriber.extract_audio") as extract,
        _popen_patch([_ok_result("ep01")]) as popen,
    ):
        done, failed = t.transcribe_files([video])

    assert done == 1 and failed == []
    # Audio is extracted INTO the work dir (and not deleted after the run)...
    assert extract.call_args.args[1] == work / "ep01.wav"
    # ...and the worker is told to keep the separated stems there too.
    cmd = popen.call_args.args[0]
    assert "--keep-vocals-dir" in cmd
    assert cmd[cmd.index("--keep-vocals-dir") + 1] == str(work)
    assert work.is_dir()


def test_transcribe_files_worker_failure_isolated_per_video(tmp_path):
    v1, v2 = tmp_path / "a.mp4", tmp_path / "b.mp4"
    v1.write_bytes(b"fake")
    v2.write_bytes(b"fake")
    results = [{"video_id": "a", "ok": False, "error": "separator exploded"}, _ok_result("b")]

    t = JaTranscriber(settings=_settings())
    with (
        mock.patch("creator_agent.asr.ja_transcriber.extract_audio"),
        _popen_patch(results),
    ):
        done, failed = t.transcribe_files([v1, v2])

    assert done == 1
    assert failed == [("a.mp4", "separator exploded")]
    assert (tmp_path / "b.srt").exists()
    assert not (tmp_path / "a.srt").exists()


def test_transcribe_files_nonzero_exit_raises(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    t = JaTranscriber(settings=_settings())
    with (
        mock.patch("creator_agent.asr.ja_transcriber.extract_audio"),
        _popen_patch(rc=2, stderr=b"traceback\n"),
    ):
        with pytest.raises(RuntimeError, match="exited 2"):
            t.transcribe_files([video])


def test_unique_id_disambiguates_duplicate_stems():
    used: set[str] = set()
    assert JaTranscriber._unique_id("ep01", used) == "ep01"
    assert JaTranscriber._unique_id("ep01", used) == "ep01_2"
    assert JaTranscriber._unique_id("ep01", used) == "ep01_3"


def test_scan_videos_finds_only_video_exts(tmp_path):
    from creator_agent.asr.ja_transcriber import scan_videos

    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mkv").write_bytes(b"x")
    (tmp_path / "c.srt").write_text("x")
    (tmp_path / "d.txt").write_text("x")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "e.MP4").write_bytes(b"x")  # case-insensitive ext, recursive

    found = scan_videos(tmp_path)
    assert [p.name for p in found] == ["a.mp4", "b.mkv", "e.MP4"]
    assert scan_videos(tmp_path / "missing") == []


def test_split_pending_by_existing_srt(tmp_path):
    from creator_agent.asr.ja_transcriber import split_pending

    v1 = tmp_path / "done.mp4"
    v2 = tmp_path / "new.mp4"
    v1.write_bytes(b"x")
    v2.write_bytes(b"x")
    (tmp_path / "done.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n")

    pending, done = split_pending([v1, v2])
    assert pending == [v2] and done == [v1]

    # out_dir redirects where the .srt is looked for (one <stem>/ subdir each).
    out = tmp_path / "out"
    out.mkdir()
    pending, done = split_pending([v1, v2], out)
    assert pending == [v1, v2] and done == []
    (out / "done").mkdir()
    (out / "done" / "done.srt").write_text("x")
    pending, done = split_pending([v1, v2], out)
    assert pending == [v2] and done == [v1]
