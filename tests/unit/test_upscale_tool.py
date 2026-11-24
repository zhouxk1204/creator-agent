from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
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
    monkeypatch.setattr(
        tool,
        "video_info",
        lambda video: {
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "duration": 1 / 30,
            "frames": 1,
            "codec": "h264",
            "estimated": False,
        },
    )
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


def test_inference_progress_uses_actual_output_frame_count(tmp_path, monkeypatch):
    for index in range(3):
        (tmp_path / f"frame_{index:06d}_out.jpg").write_bytes(b"frame")
    (tmp_path / "unrelated.jpg").write_bytes(b"other")
    (tmp_path / "frame_999999_out.jpg").mkdir()
    monkeypatch.setattr(tool.time, "monotonic", lambda: 12)
    status = tool._inference_progress(tmp_path, 123451, 10)
    assert "3/123451 frames" in status
    assert "1.50 frames/s" in status and "ETA=" in status
    assert "[----------------]" in status


@pytest.mark.parametrize("line", ["Testing 0 frame_000001", "Testing 1234 frame_001235", "Tile 1/4"])
def test_routine_inference_lines_are_hidden(line):
    assert tool._inference_output(line) is False


@pytest.mark.parametrize(
    "line",
    ["RuntimeError: CUDA out of memory", "Warning: image failed", "Testing 0 frame_000001 ERROR", "Error frame_000001"],
)
def test_inference_diagnostics_are_preserved(line):
    assert tool._inference_output(line) is True


def test_video_information_is_printed_before_frame_processing(setup, monkeypatch, capsys):
    output, videos = setup
    calls = fake_commands(monkeypatch, output)
    assert tool.main([str(videos[0]), "--output-dir", str(output)]) == 0
    text = capsys.readouterr().out
    assert text.index("[VIDEO INFO]") < text.index("[STAGE 1/3]")
    assert "resolution=1920x1080" in text and "FPS=30.000" in text and "frames=1" in text
    for command in (calls[0], calls[2]):
        assert "-nostats" in command and "-hide_banner" in command
        assert command[command.index("-loglevel") + 1] == "warning"
    assert "-progress" in calls[2] and "pipe:1" in calls[2]


def test_video_probe_reads_header_metadata_and_prefers_configured_ffprobe(tmp_path, monkeypatch):
    ffmpeg = tmp_path / "ffmpeg.exe"
    probe = tmp_path / "ffprobe.exe"
    probe.touch()
    monkeypatch.setattr(tool, "FFMPEG_EXE", ffmpeg)
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "width": 1920,
                            "height": 1080,
                            "codec_name": "h264",
                            "avg_frame_rate": "30000/1001",
                            "nb_frames": "123451",
                            "duration": "4119.15",
                        }
                    ]
                }
            ),
        )
    )
    monkeypatch.setattr(tool.subprocess, "run", run)
    info = tool.video_info(tmp_path / "source.mp4")
    assert run.call_args.args[0][0] == str(probe)
    assert "-count_frames" not in run.call_args.args[0]
    assert info["frames"] == 123451 and not info["estimated"]
    assert info["fps"] == pytest.approx(29.97003)


def test_video_probe_marks_estimates_and_uses_format_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(tool.shutil, "which", lambda name: "ffprobe")
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"avg_frame_rate": "0/0", "r_frame_rate": "30/1", "nb_frames": "N/A", "duration": "N/A"}
                    ],
                    "format": {"duration": "10"},
                }
            ),
        )
    )
    monkeypatch.setattr(tool.subprocess, "run", run)
    info = tool.video_info(tmp_path / "source.mp4")
    assert info["frames"] == 300 and info["estimated"]


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(returncode=1, stdout=""),
        SimpleNamespace(returncode=0, stdout="bad json"),
        SimpleNamespace(returncode=0, stdout='{"streams": []}'),
    ],
)
def test_bad_probe_data_does_not_block_frame_extraction(tmp_path, monkeypatch, result):
    monkeypatch.setattr(tool.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(tool.subprocess, "run", Mock(return_value=result))
    assert tool.video_info(tmp_path / "source.mp4") == {}


def test_progress_denominator_is_extracted_frames_not_header_estimate(setup, monkeypatch):
    output, videos = setup
    monkeypatch.setattr(tool, "video_info", lambda video: {"frames": 123451, "estimated": True})
    fake_commands(monkeypatch, output)
    run = tool.run
    statuses = []

    def observe(command, log_file, cwd=None, **kwargs):
        run(command, log_file, cwd, **kwargs)
        statuses.append(kwargs["progress"]())

    monkeypatch.setattr(tool, "run", observe)
    assert tool.main([str(videos[0]), "--output-dir", str(output)]) == 0
    assert "1/~123451 frames" in statuses[0]
    assert "1/1 frames" in statuses[1]
    assert "123451" not in statuses[1]


def test_encoder_status_reads_ffmpeg_frames_and_retains_errors(tmp_path):
    output = tmp_path / "video.part.mp4"
    progress = tool.EncoderProgress(123451, output)
    assert progress.output_line("frame=1234") is False
    assert progress.output_line("out_time_us=10000") is False
    assert progress.output_line("stream_0_0_q=22.0") is False
    assert progress.output_line("progress=continue") is False
    assert progress.output_line("ERROR: encoder failed") is True
    assert "1234/123451 frames" in progress.status()
    assert progress.output_line("frame=N/A") is False
    assert progress.frames == 1234


def test_upscale_runner_uses_quiet_progress_without_changing_failure_codes(tmp_path, monkeypatch):
    run = Mock(return_value=7)
    monkeypatch.setattr(tool, "run_logged", run)

    def callback():
        return "[INFERENCE] 1/2 frames"

    with pytest.raises(subprocess.CalledProcessError) as error:
        tool.run(["local-tool"], tmp_path / "log.txt", progress=callback, output_filter=tool._inference_output)
    assert error.value.returncode == 7
    assert run.call_args.kwargs["quiet"] is True
    assert run.call_args.kwargs["progress"] is callback
    assert run.call_args.kwargs["output_filter"] is tool._inference_output
