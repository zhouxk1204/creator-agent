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

    def run(command, log_file, cwd=None, **kwargs):
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

    def fail_first(command, log_file, cwd=None, **kwargs):
        if command[-1] == output / "941_1_4K.part.mp4":
            real_run(command, log_file, cwd, **kwargs)
            raise subprocess.CalledProcessError(1, command)
        real_run(command, log_file, cwd, **kwargs)

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


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_interrupt_then_resume_skips_finished_video_and_restarts_frames(setup, monkeypatch, stage):
    output, videos = setup
    complete = output / "941_1_4K.mp4"
    complete.write_bytes(b"completed video")
    calls = fake_commands(monkeypatch, output)
    fake_run = tool.run

    def interrupt(command, log_file, cwd=None, **kwargs):
        fake_run(command, log_file, cwd, **kwargs)
        if len(calls) == stage:
            raise KeyboardInterrupt

    monkeypatch.setattr(tool, "run", interrupt)
    argv = [*(str(video) for video in videos), "--output-dir", str(output)]
    assert tool.main(argv) == 130
    assert complete.read_bytes() == b"completed video"
    assert not (output / "941_2_4K.mp4").exists()
    assert not (output / ".upscale_tmp").exists()
    assert not list(output.glob("*.part.mp4"))

    # Also simulate debris from closing the terminal without a graceful interrupt.
    frames = output / ".upscale_tmp/941_2/out_frames"
    frames.mkdir(parents=True)
    (frames / "stale.jpg").write_bytes(b"unfinished frame")
    (output / "941_2_4K.part.mp4").write_bytes(b"unfinished encoding")
    resumed = fake_commands(monkeypatch, output)
    fake_run = tool.run

    def restart(command, log_file, cwd=None, **kwargs):
        if not resumed:
            assert "941_2" in str(command[-1])
            assert "in_frames" in str(command[-1])
            assert not (frames / "stale.jpg").exists()
            assert not (output / "941_2_4K.part.mp4").exists()
        fake_run(command, log_file, cwd, **kwargs)

    monkeypatch.setattr(tool, "run", restart)
    assert tool.main(argv) == 0
    assert len(resumed) == 3
    assert complete.read_bytes() == b"completed video"
    assert (output / "941_2_4K.mp4").read_bytes() == b"upscaled video"
    assert all(video.read_bytes() == b"original" for video in videos)
    text = (output / "run_log.txt").read_text(encoding="utf-8")
    assert text.count("Real-ESRGAN RUN START") == 2
    assert "[INTERRUPTED]" in text
    assert "[SKIP]" in text


def test_all_completed_videos_skip_without_gpu_dependencies(setup, monkeypatch):
    output, videos = setup
    for video in videos:
        (output / f"{video.stem}_4K.mp4").write_bytes(b"complete")
    check = Mock(side_effect=RuntimeError("GPU unavailable"))
    monkeypatch.setattr(tool, "check_setup", check)
    run = Mock()
    monkeypatch.setattr(tool, "run", run)
    assert tool.main([*(str(video) for video in videos), "--output-dir", str(output)]) == 0
    check.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize("missing_stage", [1, 2, 3])
def test_success_exit_without_complete_artifacts_is_not_a_checkpoint(setup, monkeypatch, missing_stage):
    output, videos = setup
    previous = output / "941_1_4K.mp4"
    previous.write_bytes(b"previous good output")
    calls = fake_commands(monkeypatch, output)
    fake_run = tool.run

    def missing(command, log_file, cwd=None, **kwargs):
        fake_run(command, log_file, cwd, **kwargs)
        if len(calls) == missing_stage:
            if missing_stage == 1:
                Path(command[-1]).with_name("frame_000001.jpg").unlink()
            elif missing_stage == 2:
                (Path(command[command.index("-o") + 1]) / "frame_000001_out.jpg").unlink()
            else:
                Path(command[-1]).write_bytes(b"")

    monkeypatch.setattr(tool, "run", missing)
    assert tool.main([str(videos[0]), "--output-dir", str(output), "--force"]) == 1
    assert previous.read_bytes() == b"previous good output"
    assert not (output / ".upscale_tmp").exists()
    assert not list(output.glob("*.part.mp4"))
