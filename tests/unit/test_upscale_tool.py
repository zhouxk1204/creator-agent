from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import upscale_tool as tool  # noqa: E402


@pytest.fixture
def setup(tmp_path, monkeypatch):
    output = tmp_path / "素材 output !"
    output.mkdir()
    videos = [output / f"941_{index}.mp4" for index in (1, 2)]
    for video in videos:
        video.write_bytes(b"original")
    monkeypatch.setattr(tool, "check_setup", lambda: None)
    return output, videos


def fake_commands(monkeypatch, output, *, failure_stage=None):
    calls = []

    def run(command, log_file, cwd=None):
        calls.append(command)
        stage = (len(calls) - 1) % 3 + 1
        if stage == 1:
            frame = Path(command[-1]).with_name("frame_000001.jpg")
            if "941_2" in str(frame):
                assert not (output / ".upscale_tmp/941_1").exists()
                assert not (output / "941_1_4K.part.mp4").exists()
            frame.write_bytes(b"original frame")
        elif stage == 2:
            (Path(command[command.index("-o") + 1]) / "frame_000001_out.jpg").write_bytes(b"upscaled frame")
        else:
            Path(command[-1]).write_bytes(b"upscaled video")
        if failure_stage == stage:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(tool, "run", run)
    return calls


def test_only_explicit_videos_are_upscaled_and_cleaned_before_the_next(setup, monkeypatch):
    output, videos = setup
    unrelated = output / "old_3.mp4"
    unrelated.write_bytes(b"not part of this run")
    calls = fake_commands(monkeypatch, output)
    assert tool.main([*(str(video) for video in videos), "--output-dir", str(output)]) == 0
    assert len(calls) == 6
    assert (output / "941_1_4K.mp4").read_bytes() == b"upscaled video"
    assert (output / "941_2_4K.mp4").read_bytes() == b"upscaled video"
    assert not (output / "old_3_4K.mp4").exists()
    assert not (output / ".upscale_tmp").exists()
    assert not list(output.glob("*.part.mp4"))
    assert all(video.read_bytes() == b"original" for video in videos)
    assert (output / "run_log.txt").is_file()
    assert str(output) in str(calls[0][-1])


@pytest.mark.parametrize("failure_stage", [1, 2, 3])
def test_failure_removes_frames_and_partial_video_without_replacing_existing(setup, monkeypatch, failure_stage):
    output, videos = setup
    complete = output / "941_1_4K.mp4"
    complete.write_bytes(b"previous good output")
    fake_commands(monkeypatch, output, failure_stage=failure_stage)
    assert tool.main([str(videos[0]), "--output-dir", str(output), "--force"]) == 1
    assert complete.read_bytes() == b"previous good output"
    assert not (output / ".upscale_tmp").exists()
    assert not list(output.glob("*.part.mp4"))
    assert videos[0].read_bytes() == b"original"


def test_empty_output_is_regenerated_instead_of_skipped(setup, monkeypatch):
    output, videos = setup
    (output / "941_1_4K.mp4").touch()
    fake_commands(monkeypatch, output)
    assert tool.main([str(videos[0]), "--output-dir", str(output)]) == 0
    assert (output / "941_1_4K.mp4").read_bytes() == b"upscaled video"
    assert not (output / ".upscale_tmp").exists()


def test_existing_output_skip_cleans_stale_temporary_files(setup, monkeypatch):
    output, videos = setup
    (output / "941_1_4K.mp4").write_bytes(b"done")
    (output / "941_1_4K.part.mp4").write_bytes(b"partial")
    frames = output / ".upscale_tmp/941_1/in_frames"
    frames.mkdir(parents=True)
    (frames / "old.jpg").write_bytes(b"frame")
    run = Mock()
    monkeypatch.setattr(tool, "run", run)
    assert tool.main([str(videos[0]), "--output-dir", str(output)]) == 0
    run.assert_not_called()
    assert not (output / ".upscale_tmp").exists()
    assert not list(output.glob("*.part.mp4"))


def test_one_failed_video_does_not_block_the_second(setup, monkeypatch):
    output, videos = setup
    calls = fake_commands(monkeypatch, output)
    real_run = tool.run

    def fail_first(command, log_file, cwd=None):
        if command[-1] == output / "941_1_4K.part.mp4":
            real_run(command, log_file, cwd)
            raise subprocess.CalledProcessError(1, command)
        real_run(command, log_file, cwd)

    monkeypatch.setattr(tool, "run", fail_first)
    assert tool.main([*(str(video) for video in videos), "--output-dir", str(output)]) == 1
    assert len(calls) == 6
    assert not (output / "941_1_4K.mp4").exists()
    assert (output / "941_2_4K.mp4").read_bytes() == b"upscaled video"
    assert not (output / ".upscale_tmp").exists()


def test_missing_explicit_input_fails_without_processing_the_directory(setup, monkeypatch):
    output, videos = setup
    run = Mock()
    monkeypatch.setattr(tool, "run", run)
    assert tool.main([str(videos[0]), str(output / "941_3.mp4"), "--output-dir", str(output)]) == 1
    run.assert_not_called()


def test_custom_work_directory_preserves_unrelated_contents(setup, monkeypatch, tmp_path):
    output, videos = setup
    work = tmp_path / "work"
    work.mkdir()
    unrelated = work / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    fake_commands(monkeypatch, output)
    assert tool.main([str(videos[0]), "--output-dir", str(output), "--work-dir", str(work)]) == 0
    assert set(work.iterdir()) == {unrelated}


def test_frame_directory_creation_failure_still_cleans_workspace(setup, monkeypatch):
    output, videos = setup
    original = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path.name == "out_frames":
            raise OSError("disk full")
        original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    assert tool.main([str(videos[0]), "--output-dir", str(output)]) == 1
    assert not (output / ".upscale_tmp").exists()
