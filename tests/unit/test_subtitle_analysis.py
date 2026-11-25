"""Exercise stage 0 with a fake decoder, never with an actual video."""

import csv
import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from creator_agent.subtitle_extractor import analysis, cli
from creator_agent.subtitle_extractor.analysis import AnalysisResult, analyze_video
from creator_agent.subtitle_extractor.timeline import VideoAnalysisError


class FakeFrame:
    def __init__(self, pts, duration=40, *, time_base=Fraction(1, 1000)):
        self.pts = pts
        self.duration = duration
        self.time_base = time_base
        self.width = 32
        self.height = 24
        self.is_corrupt = False

    def to_image(self):
        # The test only checks that frame paths are unique and tied to the index.
        return SimpleNamespace(save=lambda path: Path(path).write_bytes(b"fake PNG"))


class FakeContainer:
    def __init__(self, frames, *, expected=None, failure=None):
        self.frames = frames
        self.failure = failure
        self.closed = False
        self.duration = 120000
        self.stream = SimpleNamespace(
            time_base=Fraction(1, 1000),
            average_rate=Fraction(25),
            start_time=0,
            duration=120,
            frames=len(frames) if expected is None else expected,
            codec_context=SimpleNamespace(width=32, height=24, name="fake_codec"),
            index=0,
        )
        self.streams = SimpleNamespace(video=[self.stream])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def decode(self, stream):
        assert stream is self.stream
        yield from self.frames
        if self.failure is not None:
            raise self.failure


@pytest.fixture
def input_path(tmp_path):
    path = tmp_path / "episode.mp4"
    path.write_bytes(b"not a real video; the decoder is mocked")
    return path


@pytest.fixture
def install_decoder(monkeypatch):
    def install(frames=None, **kwargs):
        container = FakeContainer(
            frames if frames is not None else [FakeFrame(0), FakeFrame(40), FakeFrame(80)], **kwargs
        )
        monkeypatch.setattr(
            analysis, "_load_av", lambda: SimpleNamespace(open=lambda path: container, time_base=1000000)
        )
        return container

    return install


def read_index(output):
    with (output / "frame_index.csv").open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_full_index_outputs_and_unique_frame_images(input_path, tmp_path, install_decoder):
    container = install_decoder()
    progress = []
    output = tmp_path / "task"
    result = analyze_video(input_path, output_dir=output, on_progress=lambda *args: progress.append(args))
    rows = read_index(output)
    assert result.output_dir == output
    assert container.closed
    assert len(rows) == result.video_info["frame_count"] == result.video_info["index_record_count"] == 3
    assert [row["frame_index"] for row in rows] == ["0", "1", "2"]
    assert [row["pts_time"] for row in rows] == ["0.000000000", "0.040000000", "0.080000000"]
    assert rows[-1]["duration"] == "0.040000000"
    assert rows[1]["raw_pts"] == "40"
    assert rows[1]["time_base"] == "1/1000"
    assert all((output / row["image_path"]).is_file() for row in rows)
    assert len(list((output / "frames").glob("*.png"))) == 3
    assert progress == [(1, 3), (2, 3), (3, 3)]
    info = read_json(output / "video_info.json")
    assert info["frame_rate_mode"] == "CFR"
    assert info["codec"] == "fake_codec"
    assert info["last_display_end"] == pytest.approx(0.12)
    assert info["decoded_duration"] == pytest.approx(0.12)
    assert read_json(output / "task_status.json")["status"] == "completed"
    assert (output / "timestamp_anomalies.jsonl").read_text() == ""
    assert "completed" in (output / "analysis.log").read_text()


def test_no_save_frames_does_not_call_image_conversion(input_path, tmp_path, install_decoder):
    frame = FakeFrame(0)
    frame.to_image = lambda: pytest.fail("image conversion must not run")
    install_decoder([frame])
    output = tmp_path / "index_only"
    analyze_video(input_path, output_dir=output, save_frames=False)
    assert not (output / "frames").exists()
    assert read_index(output)[0]["image_path"] == ""


def test_anomalies_are_traceable_and_nonzero_pts_remain_original(input_path, tmp_path, install_decoder):
    container = install_decoder([FakeFrame(5000), FakeFrame(None), FakeFrame(5040)])
    container.stream.start_time = 5000
    output = tmp_path / "anomalies"
    info = analyze_video(input_path, output_dir=output, save_frames=False).video_info
    rows = read_index(output)
    assert rows[0]["pts_time"] == "5.000000000"
    assert rows[1]["pts_time"] == "5.040000000"
    assert rows[1]["raw_pts"] == ""
    assert rows[1]["pts_source"] == "previous_frame_duration"
    assert info["frame_rate_mode"] == "unknown"
    events = [json.loads(line) for line in (output / "timestamp_anomalies.jsonl").read_text().splitlines()]
    assert {(event["code"], event["frame_index"]) for event in events} >= {("missing_pts", 1), ("duplicate_pts", 2)}


def test_vfr_without_decoder_duration_uses_pts_intervals(input_path, tmp_path, install_decoder):
    container = install_decoder([FakeFrame(0, 0), FakeFrame(40, 0), FakeFrame(100, 0)])
    container.stream.duration = 130
    output = tmp_path / "vfr"
    result = analyze_video(input_path, output_dir=output, save_frames=False)
    assert result.video_info["frame_rate_mode"] == "VFR"
    assert [row["duration"] for row in read_index(output)] == ["0.040000000", "0.060000000", "0.030000000"]
    assert read_index(output)[-1]["duration_source"] == "stream_end"


def test_expected_count_is_only_a_hint_and_mismatch_is_reported(input_path, tmp_path, install_decoder):
    install_decoder(expected=20)
    output = tmp_path / "count_mismatch"
    info = analyze_video(input_path, output_dir=output, save_frames=False).video_info
    assert info["expected_frame_count"] == 20
    assert info["frame_count"] == 3
    assert info["anomaly_counts"]["frame_count_mismatch"] == 1


def test_unknown_metadata_duration_is_computed_from_decoded_display_times(input_path, tmp_path, install_decoder):
    container = install_decoder(expected=0)
    container.stream.duration = None
    container.duration = None
    info = analyze_video(input_path, output_dir=tmp_path / "unknown", save_frames=False).video_info
    assert info["expected_frame_count"] is None
    assert info["duration"] == pytest.approx(0.12)


def test_decode_failure_keeps_partial_index_and_marks_task_failed(input_path, tmp_path, install_decoder):
    container = install_decoder(failure=RuntimeError("decoder broke"))
    output = tmp_path / "failure"
    with pytest.raises(VideoAnalysisError, match="decoder broke"):
        analyze_video(input_path, output_dir=output, save_frames=False)
    assert container.closed
    assert len(read_index(output)) == 2
    task = read_json(output / "task_status.json")
    assert task["status"] == "failed"
    assert task["decoded_frames"] == 3
    assert task["index_records"] == 2
    assert "decoder broke" in task["error"]
    assert read_json(output / "video_info.json")["status"] == "failed"
    assert "decoder broke" in (output / "analysis.log").read_text()
    before = (output / "frame_index.csv").read_bytes()
    with pytest.raises(VideoAnalysisError, match="preserved"):
        analyze_video(input_path, output_dir=output)
    assert (output / "frame_index.csv").read_bytes() == before


def test_interruption_is_marked_and_preserves_data(input_path, tmp_path, install_decoder):
    install_decoder(failure=KeyboardInterrupt())
    output = tmp_path / "interrupt"
    with pytest.raises(KeyboardInterrupt):
        analyze_video(input_path, output_dir=output, save_frames=False)
    assert read_json(output / "task_status.json")["status"] == "interrupted"
    assert len(read_index(output)) == 2


def test_empty_decoded_stream_is_not_success(input_path, tmp_path, install_decoder):
    install_decoder([])
    output = tmp_path / "empty"
    with pytest.raises(VideoAnalysisError, match="did not yield"):
        analyze_video(input_path, output_dir=output)
    assert read_json(output / "task_status.json")["status"] == "failed"


def test_image_save_error_marks_failure(input_path, tmp_path, install_decoder):
    frame = FakeFrame(0)
    frame.to_image = lambda: (_ for _ in ()).throw(OSError("disk full"))
    install_decoder([frame])
    output = tmp_path / "disk_failure"
    with pytest.raises(VideoAnalysisError, match="disk full"):
        analyze_video(input_path, output_dir=output)
    assert read_json(output / "task_status.json")["status"] == "failed"


def test_default_output_and_existing_empty_directory(input_path, tmp_path, install_decoder, monkeypatch):
    install_decoder()
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "output" / "episode"
    output.mkdir(parents=True)
    result = analyze_video(input_path, save_frames=False)
    assert result.output_dir == output


def test_existing_results_are_never_overwritten(input_path, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "result.srt"
    sentinel.write_text("keep me", encoding="utf-8")
    with pytest.raises(VideoAnalysisError, match="empty directory"):
        analyze_video(input_path, output_dir=output)
    assert sentinel.read_text() == "keep me"
    assert len(list(output.iterdir())) == 1


def test_output_file_is_rejected(input_path, tmp_path):
    output = tmp_path / "output_file"
    output.write_text("keep me")
    with pytest.raises(VideoAnalysisError, match="empty directory"):
        analyze_video(input_path, output_dir=output)
    assert output.read_text() == "keep me"


def test_missing_input_fails_before_creating_output(tmp_path):
    output = tmp_path / "task"
    with pytest.raises(VideoAnalysisError, match="does not exist"):
        analyze_video(tmp_path / "missing.mp4", output_dir=output)
    assert not output.exists()


def test_missing_optional_dependency_has_install_instructions(input_path, tmp_path, monkeypatch):
    def unavailable(name):
        raise ImportError(name)

    monkeypatch.setattr(analysis.importlib, "import_module", unavailable)
    with pytest.raises(VideoAnalysisError, match="uv sync --extra subtitle-extractor"):
        analyze_video(input_path, output_dir=tmp_path / "task")


def test_no_video_stream_fails_without_outputs(input_path, tmp_path, install_decoder):
    container = install_decoder()
    container.streams.video = []
    output = tmp_path / "audio_only"
    with pytest.raises(VideoAnalysisError, match="no video stream"):
        analyze_video(input_path, output_dir=output)
    assert container.closed
    assert not output.exists()


def test_missing_stream_time_base_fails_clearly(input_path, tmp_path, install_decoder):
    container = install_decoder()
    container.stream.time_base = None
    with pytest.raises(VideoAnalysisError, match="no time_base"):
        analyze_video(input_path, output_dir=tmp_path / "bad_clock")


def test_corrupt_frame_and_resolution_change_are_logged(input_path, tmp_path, install_decoder):
    frame = FakeFrame(0)
    frame.width = 64
    frame.is_corrupt = True
    install_decoder([frame])
    info = analyze_video(input_path, output_dir=tmp_path / "corrupt", save_frames=False).video_info
    assert info["anomaly_counts"]["corrupt_frame"] == 1
    assert info["anomaly_counts"]["frame_size_changed"] == 1


def test_cli_help_does_not_require_pyav():
    result = CliRunner().invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "--input" in result.output
    assert "--no-save-frames" in result.output


def test_cli_success_arguments_and_output(input_path, tmp_path, monkeypatch):
    calls = []
    output = tmp_path / "task"

    def fake_analyze(path, **kwargs):
        calls.append((path, kwargs))
        kwargs["on_progress"](3, None)
        return AnalysisResult(output, {"frame_count": 3, "frame_rate_mode": "VFR", "anomaly_counts": {}})

    monkeypatch.setattr(cli, "analyze_video", fake_analyze)
    result = CliRunner().invoke(
        cli.app,
        [
            "--input",
            str(input_path),
            "--output",
            str(output),
            "--no-save-frames",
            "--fallback-fps",
            "23.976",
            "--gap-warning-seconds",
            "0.5",
            "--cfr-tolerance",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Stage 0 completed" in result.output
    assert "VFR" in result.output
    assert calls[0][0] == input_path
    assert calls[0][1]["save_frames"] is False
    assert calls[0][1]["fallback_fps"] == 23.976
    assert calls[0][1]["gap_warning_seconds"] == 0.5
    assert calls[0][1]["cfr_tolerance"] == 0


def test_cli_domain_error_is_reported(input_path, monkeypatch):
    def failed(*args, **kwargs):
        raise VideoAnalysisError("decode failed")

    monkeypatch.setattr(cli, "analyze_video", failed)
    result = CliRunner().invoke(cli.app, ["--input", str(input_path), "--no-save-frames"])
    assert result.exit_code == 1
    assert "Stage 0 failed: decode failed" in result.output


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--fallback-fps", "0"),
        ("--fallback-fps", "nan"),
        ("--fallback-fps", "inf"),
        ("--gap-warning-seconds", "-1"),
        ("--cfr-tolerance", "-0.1"),
    ],
)
def test_cli_invalid_timing_parameters(input_path, flag, value):
    result = CliRunner().invoke(cli.app, ["--input", str(input_path), flag, value])
    assert result.exit_code == 2
    assert "must be finite" in result.output


def test_cli_missing_input_is_a_usage_error(tmp_path):
    result = CliRunner().invoke(cli.app, ["--input", str(tmp_path / "missing.mp4")])
    assert result.exit_code == 2


def test_exact_timing_fractions_can_be_reconstructed(input_path, tmp_path, install_decoder):
    clock = Fraction(1, 24000)
    frames = [FakeFrame(pts, 1001, time_base=clock) for pts in (0, None, 2002)]
    container = install_decoder(frames)
    container.stream.time_base = clock
    output = tmp_path / "exact"
    analyze_video(input_path, output_dir=output, save_frames=False)
    rows = read_index(output)
    inferred_pts = Fraction(int(rows[1]["pts_numerator"]), int(rows[1]["pts_denominator"]))
    duration = Fraction(int(rows[1]["duration_numerator"]), int(rows[1]["duration_denominator"]))
    assert inferred_pts == duration == Fraction(1001, 24000)


def test_unknown_stream_start_does_not_invent_a_stream_endpoint(input_path, tmp_path, install_decoder):
    container = install_decoder([FakeFrame(1000, 0), FakeFrame(1040, 0), FakeFrame(1080, 0)])
    container.stream.start_time = None
    container.stream.duration = 2000
    output = tmp_path / "unknown_start"
    analyze_video(input_path, output_dir=output, save_frames=False)
    assert read_index(output)[-1]["duration"] == "0.040000000"
    assert read_index(output)[-1]["duration_source"] == "previous_pts_interval"


def test_concurrent_output_claim_does_not_overwrite_another_task(input_path, tmp_path, install_decoder, monkeypatch):
    install_decoder()
    original_load = analysis._load_av
    output = tmp_path / "race"

    def claim_before_open():
        output.mkdir()
        (output / "task_status.json").write_text("another task owns this", encoding="utf-8")
        return original_load()

    monkeypatch.setattr(analysis, "_load_av", claim_before_open)
    with pytest.raises(VideoAnalysisError, match="Another task"):
        analyze_video(input_path, output_dir=output, save_frames=False)
    assert (output / "task_status.json").read_text() == "another task owns this"
    assert not (output / "frame_index.csv").exists()


def test_missing_frame_clock_fallback_is_logged(input_path, tmp_path, install_decoder):
    install_decoder([FakeFrame(0, time_base=None)])
    output = tmp_path / "missing_frame_clock"
    info = analyze_video(input_path, output_dir=output, save_frames=False).video_info
    assert read_index(output)[0]["time_base"] == "1/1000"
    assert info["anomaly_counts"]["frame_time_base_from_stream"] == 1


def test_unreadable_input_is_reported_without_creating_output(input_path, tmp_path, monkeypatch):
    def failed_open(path):
        raise RuntimeError("invalid media format")

    monkeypatch.setattr(analysis, "_load_av", lambda: SimpleNamespace(open=failed_open))
    output = tmp_path / "unreadable"
    with pytest.raises(VideoAnalysisError, match="invalid media format"):
        analyze_video(input_path, output_dir=output)
    assert not output.exists()
