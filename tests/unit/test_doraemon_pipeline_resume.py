from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import download_doraemon_materials as pipeline  # noqa: E402


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.fetch_doraemon, "STATE_DIR", tmp_path / "logs")
    monkeypatch.setattr(pipeline.fetch_doraemon, "DESKTOP_DIR", tmp_path / "测试 Desktop !")
    monkeypatch.setattr(pipeline, "UPSCALE_PYTHON", Path(sys.executable))
    latest = Mock(return_value={"id": "0942", "date": "2026-11-14"})
    monkeypatch.setattr(pipeline.fetch_doraemon, "_latest_item", latest)
    proc = Mock()
    proc.wait.return_value = 0
    monkeypatch.setattr(pipeline, "LoggedProcess", Mock(return_value=proc))
    calls = []

    def run(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        if name == "split_episode.py":
            source = Path(args[0])
            for index in (1, 2):
                (source.parent / f"{source.stem}_{index}.mp4").write_bytes(b"split")
        elif name == "upscale_tool.py":
            output = Path(args[args.index("--output-dir") + 1])
            for video in args[:2]:
                (output / f"{Path(video).stem}_4K.mp4").write_bytes(b"complete")
        return 0

    monkeypatch.setattr(pipeline, "_run_script", run)
    return latest, proc, calls


def save_pending(status="interrupted", *, marker=None):
    materials = pipeline.fetch_doraemon.desktop_episode_dir("941").resolve()
    pipeline._save_state(materials, "941", "2026-11-07", "upscale", status)
    marker_path = pipeline.fetch_doraemon.STATE_DIR / (marker or pipeline.PENDING_MARKER)
    pipeline._atomic_text(marker_path, str(materials) + "\n")
    return materials


def read_state(materials):
    return json.loads((materials / pipeline.STATE_NAME).read_text(encoding="utf-8"))


@pytest.mark.parametrize("status", sorted(pipeline.PENDING_STATUSES))
def test_pending_episode_wins_without_latest_network_lookup(offline, status):
    latest, proc, calls = offline
    materials = save_pending(status)
    latest.side_effect = AssertionError("resume must not query the network story index")
    assert pipeline.main([]) == 0
    latest.assert_not_called()
    assert calls[0][1][-2:] == ("--broadcast-date", "2026-11-07")
    assert all("942" not in str(call) for call in calls)
    assert read_state(materials)["status"] == "completed"
    assert pipeline._pending_run() is None
    proc.wait.assert_called_once_with()


def test_legacy_materials_marker_is_accepted(offline):
    materials = save_pending(marker=".last_doraemon_materials")
    assert pipeline._pending_run() == ("941", "2026-11-07", materials)


def test_completed_episode_selects_latest_instead(offline):
    latest, _, calls = offline
    save_pending("completed")
    assert pipeline.main([]) == 0
    latest.assert_called_once_with()
    assert calls[0][1][-1] == "2026-11-14"


def test_latest_flag_bypasses_saved_task(offline):
    latest, _, calls = offline
    old = save_pending()
    assert pipeline.main(["--latest"]) == 0
    latest.assert_called_once_with()
    assert calls[0][1][-1] == "2026-11-14"
    assert read_state(old)["status"] == "interrupted"


def test_materials_only_fetch_does_not_hide_pending_full_pipeline(offline):
    latest, _, calls = offline
    old = save_pending()
    assert pipeline.main(["--no-video"]) == 0
    latest.assert_called_once_with()
    assert [call[0] for call in calls] == ["fetch_doraemon.py"]
    assert calls[0][1] == ("942",)
    assert read_state(old)["status"] == "interrupted"
    assert pipeline._pending_run() == ("941", "2026-11-07", old)


@pytest.mark.parametrize(
    "bad_state",
    [
        "not json",
        "[]",
        '{"schema_version": 2}',
        '{"schema_version": 1, "episode": "oops"}',
        '{"schema_version": 1, "episode": "941", "broadcast_date": "invalid"}',
    ],
)
def test_corrupt_checkpoint_warns_and_falls_back_to_latest(offline, bad_state, capsys):
    latest, _, _ = offline
    materials = save_pending()
    (materials / pipeline.STATE_NAME).write_text(bad_state, encoding="utf-8")
    assert pipeline.main([]) == 0
    latest.assert_called_once_with()
    assert "Cannot resume saved task" in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["download", "split", "upscale"])
def test_stage_interrupt_is_saved_and_fetch_is_stopped(offline, monkeypatch, stage):
    latest, proc, _ = offline
    materials = save_pending()
    script = {"download": "download_tver.py", "split": "split_episode.py", "upscale": "upscale_tool.py"}[stage]
    real_run = pipeline._run_script

    def run(name, *args, **kwargs):
        if name == script:
            raise KeyboardInterrupt
        return real_run(name, *args, **kwargs)

    monkeypatch.setattr(pipeline, "_run_script", run)
    assert pipeline.main([]) == 130
    assert read_state(materials)["stage"] == stage
    assert read_state(materials)["status"] == "interrupted"
    assert pipeline._pending_run() == ("941", "2026-11-07", materials)
    latest.assert_not_called()
    proc.stop.assert_called_once_with()
    proc.wait.assert_not_called()


def test_interrupt_while_waiting_for_materials_does_not_mark_completed(offline):
    _, proc, _ = offline
    materials = save_pending()
    proc.wait.side_effect = KeyboardInterrupt
    assert pipeline.main([]) == 130
    assert read_state(materials)["stage"] == "materials"
    assert read_state(materials)["status"] == "interrupted"
    assert pipeline._pending_run() == ("941", "2026-11-07", materials)
    proc.stop.assert_called_once_with()


def test_returned_interrupt_code_stops_materials_without_waiting(offline, monkeypatch):
    _, proc, _ = offline
    materials = save_pending()
    monkeypatch.setattr(pipeline, "_run_script", Mock(return_value=130))
    assert pipeline.main([]) == 130
    assert read_state(materials)["status"] == "interrupted"
    proc.stop.assert_called_once_with()
    proc.wait.assert_not_called()


def test_materials_failure_remains_pending_and_finished_videos_are_reused(offline, monkeypatch):
    _, proc, calls = offline
    materials = save_pending()
    proc.wait.return_value = 3
    assert pipeline.main([]) == 0  # Materials warnings do not change the video's exit code.
    assert read_state(materials)["status"] == "failed"
    assert read_state(materials)["stage"] == "materials"
    previous = [(materials / f"941_{i}_4K.mp4").read_bytes() for i in (1, 2)]
    calls.clear()
    proc.wait.return_value = 0
    monkeypatch.setattr(pipeline, "UPSCALE_PYTHON", materials / "missing_gpu_python.exe")
    assert pipeline.main([]) == 0
    assert [call[0] for call in calls] == ["download_tver.py", "split_episode.py"]
    assert [(materials / f"941_{i}_4K.mp4").read_bytes() for i in (1, 2)] == previous
    assert read_state(materials)["status"] == "completed"


def test_checkpoint_stays_running_until_parallel_materials_finish(offline):
    _, proc, _ = offline
    materials = save_pending()

    def finish():
        assert read_state(materials)["status"] == "running"
        assert read_state(materials)["stage"] == "materials"
        return 0

    proc.wait.side_effect = finish
    assert pipeline.main([]) == 0
    assert read_state(materials)["status"] == "completed"


def test_split_cancellation_remains_resumable(offline, monkeypatch):
    _, _, _ = offline
    materials = save_pending()
    monkeypatch.setattr(pipeline, "_run_script", Mock(side_effect=[0, 2]))
    assert pipeline.main([]) == 0
    assert read_state(materials)["status"] == "cancelled"
    assert pipeline._pending_run() == ("941", "2026-11-07", materials)


def test_split_script_keeps_inherited_stdin_for_confirmation(monkeypatch):
    run = Mock(return_value=0)
    monkeypatch.setattr(pipeline, "run_logged", run)
    assert pipeline._run_script("split_episode.py", "story.mp4", "--confirm-anomaly") == 0
    assert run.call_args.kwargs["interactive"] is True
