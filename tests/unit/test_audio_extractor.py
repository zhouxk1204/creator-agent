from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from creator_agent.asr.audio_extractor import extract_audio, find_ffmpeg


def test_find_ffmpeg_uses_explicit_path():
    assert find_ffmpeg("/custom/ffmpeg") == "/custom/ffmpeg"


def test_find_ffmpeg_resolves_from_path():
    with mock.patch("creator_agent.asr.audio_extractor.shutil.which", return_value="/usr/bin/ffmpeg"):
        assert find_ffmpeg() == "/usr/bin/ffmpeg"


def test_find_ffmpeg_raises_when_missing():
    with mock.patch("creator_agent.asr.audio_extractor.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="ffmpeg not found"):
            find_ffmpeg()


def test_extract_audio_skips_when_output_exists(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    out = tmp_path / ".wav"
    out.write_bytes(b"existing")
    with mock.patch("creator_agent.asr.audio_extractor.subprocess.run") as run:
        result = extract_audio(video, out)
        run.assert_not_called()  # idempotent: no ffmpeg call
    assert result == out


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
