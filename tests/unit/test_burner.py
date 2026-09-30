from __future__ import annotations

from unittest import mock

import pytest

from creator_agent.config import TranslateSettings
from creator_agent.translate.burner import burn_subtitles


def test_burn_command_uses_cwd_relative_srt(tmp_path):
    video = tmp_path / "ep.mp4"
    srt = tmp_path / "ep.zh.srt"
    out = tmp_path / "ep.zh.mp4"
    video.write_bytes(b"x")
    srt.write_text("x", encoding="utf-8")
    settings = TranslateSettings(ffmpeg_path="/usr/bin/ffmpeg")

    def fake_run(cmd, cwd=None, capture_output=None):
        out.write_bytes(b"done")
        return mock.Mock(returncode=0, stderr=b"")

    with mock.patch("creator_agent.translate.burner.subprocess.run", side_effect=fake_run) as run:
        result = burn_subtitles(video, srt, out, settings)

    assert result == out
    cmd = run.call_args.args[0]
    assert cmd[0] == "/usr/bin/ffmpeg"
    vf = cmd[cmd.index("-vf") + 1]
    # bare filename (no drive-letter escaping issues), cwd points at the SRT dir
    assert vf.startswith("subtitles=ep.zh.srt:force_style=")
    assert "Microsoft YaHei" in vf
    assert run.call_args.kwargs["cwd"] == tmp_path
    assert cmd[cmd.index("-c:a") + 1] == "copy"


def test_burn_raises_on_ffmpeg_failure(tmp_path):
    video = tmp_path / "ep.mp4"
    srt = tmp_path / "ep.zh.srt"
    video.write_bytes(b"x")
    srt.write_text("x", encoding="utf-8")

    with (
        mock.patch(
            "creator_agent.translate.burner.subprocess.run",
            return_value=mock.Mock(returncode=1, stderr=b"boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        burn_subtitles(video, srt, tmp_path / "out.mp4", TranslateSettings(ffmpeg_path="/usr/bin/ffmpeg"))


def test_burn_requires_ffmpeg():
    settings = TranslateSettings(ffmpeg_path="")
    with (
        mock.patch("creator_agent.translate.burner.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="ffmpeg not found"),
    ):
        burn_subtitles("v.mp4", "s.srt", "o.mp4", settings)
