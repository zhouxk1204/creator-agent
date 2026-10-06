from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from creator_agent.asr.audio_extractor import extract_audio, find_ffmpeg


def _write_sidecar(video: Path, out: Path) -> None:
    st = video.stat()
    (out.parent / (out.name + ".src.json")).write_text(
        json.dumps({"source": str(video), "size": st.st_size, "mtime_ns": st.st_mtime_ns}),
        encoding="utf-8",
    )


def test_find_ffmpeg_uses_explicit_path():
    assert find_ffmpeg("/custom/ffmpeg") == "/custom/ffmpeg"


def test_find_ffmpeg_resolves_from_path():
    with mock.patch("creator_agent.asr.audio_extractor.shutil.which", return_value="/usr/bin/ffmpeg"):
        assert find_ffmpeg() == "/usr/bin/ffmpeg"


def test_find_ffmpeg_raises_when_missing():
    with mock.patch("creator_agent.asr.audio_extractor.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="ffmpeg not found"):
            find_ffmpeg()


def test_extract_audio_skips_when_cache_valid(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    out = tmp_path / ".wav"
    out.write_bytes(b"existing")
    _write_sidecar(video, out)
    with mock.patch("creator_agent.asr.audio_extractor.subprocess.run") as run:
        result = extract_audio(video, out)
        run.assert_not_called()  # cache hit: no ffmpeg call
    assert result == out


def test_extract_audio_reextracts_without_sidecar(tmp_path):
    # An output WAV with no sidecar is NOT a valid cache entry (it may come
    # from a different, same-named video) -> must re-extract.
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    out = tmp_path / ".wav"
    out.write_bytes(b"stale")

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"wav")
        return mock.Mock(returncode=0, stderr="", stdout="")

    with (
        mock.patch("creator_agent.asr.audio_extractor.find_ffmpeg", return_value="ffmpeg"),
        mock.patch("creator_agent.asr.audio_extractor.subprocess.run", side_effect=fake_run) as run,
    ):
        extract_audio(video, out)
    run.assert_called_once()
    assert out.read_bytes() == b"wav"


def test_extract_audio_reextracts_when_source_changed(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"v1")
    out = tmp_path / ".wav"
    out.write_bytes(b"stale")
    _write_sidecar(video, out)
    video.write_bytes(b"v2-longer")  # same name, different content

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"wav")
        return mock.Mock(returncode=0, stderr="", stdout="")

    with (
        mock.patch("creator_agent.asr.audio_extractor.find_ffmpeg", return_value="ffmpeg"),
        mock.patch("creator_agent.asr.audio_extractor.subprocess.run", side_effect=fake_run) as run,
    ):
        extract_audio(video, out)
    run.assert_called_once()


def test_extract_audio_force_reextracts_even_with_valid_cache(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    out = tmp_path / ".wav"
    out.write_bytes(b"existing")
    _write_sidecar(video, out)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"wav")
        return mock.Mock(returncode=0, stderr="", stdout="")

    with (
        mock.patch("creator_agent.asr.audio_extractor.find_ffmpeg", return_value="ffmpeg"),
        mock.patch("creator_agent.asr.audio_extractor.subprocess.run", side_effect=fake_run) as run,
    ):
        extract_audio(video, out, force=True)
    run.assert_called_once()


def test_extract_audio_raises_when_video_missing(tmp_path):
    with pytest.raises(RuntimeError, match="Video file not found"):
        extract_audio(tmp_path / "missing.mp4", tmp_path / ".wav")


def test_extract_audio_invokes_ffmpeg_and_returns_path(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    out = tmp_path / ".wav"

    def fake_run(cmd, **kwargs):
        # Simulate ffmpeg creating the output file.
        Path(cmd[-1]).write_bytes(b"wav")
        return mock.Mock(returncode=0, stderr="", stdout="")

    with (
        mock.patch("creator_agent.asr.audio_extractor.find_ffmpeg", return_value="ffmpeg"),
        mock.patch("creator_agent.asr.audio_extractor.subprocess.run", side_effect=fake_run) as run,
    ):
        result = extract_audio(video, out)
    assert result == out
    assert out.exists()
    cmd = run.call_args.args[0]
    assert "-ar" in cmd and "16000" in cmd  # 16 kHz
    assert "-ac" in cmd and "1" in cmd  # mono
    assert "pcm_s16le" in cmd


def test_extract_audio_raises_on_ffmpeg_failure(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    with (
        mock.patch("creator_agent.asr.audio_extractor.find_ffmpeg", return_value="ffmpeg"),
        mock.patch(
            "creator_agent.asr.audio_extractor.subprocess.run",
            return_value=mock.Mock(returncode=1, stderr="bad input"),
        ),
    ):
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            extract_audio(video, tmp_path / ".wav")
