from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import download_doraemon_materials as pipeline  # noqa: E402


@pytest.fixture
def materials(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.fetch_doraemon, "DESKTOP_DIR", tmp_path / "测试 Desktop !")
    monkeypatch.setattr(pipeline.fetch_doraemon, "STATE_DIR", tmp_path / "logs")
    monkeypatch.setattr(pipeline.fetch_doraemon, "_latest_item", lambda: {"id": "0941", "date": "2026-11-07"})
    return tmp_path / "测试 Desktop !" / "doraemon_941"


def test_real_subprocess_fetch_and_download_overlap(tmp_path, monkeypatch, materials):
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    monkeypatch.setattr(pipeline, "ROOT", root)
    monkeypatch.setattr(pipeline, "UPSCALE_PYTHON", Path(sys.executable))
    fetch_started = root / "fetch_started"
    download_started = root / "download_started"
    fetch_finished = root / "fetch_finished"
    wait_code = (
        "import time\n"
        "def wait(path):\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not path.exists():\n"
        "        if time.monotonic() >= deadline: raise RuntimeError('branches did not overlap')\n"
        "        time.sleep(0.01)\n"
    )
    (scripts / "fetch_doraemon.py").write_text(
        "from pathlib import Path\n"
        + wait_code
        + f"Path({str(fetch_started)!r}).touch()\n"
        + f"wait(Path({str(download_started)!r}))\n"
        + f"Path({str(fetch_finished)!r}).touch()\n"
        + "print('covers finished')\n",
        encoding="utf-8",
    )
    (scripts / "download_tver.py").write_text(
        "from pathlib import Path\n"
        + wait_code
        + f"wait(Path({str(fetch_started)!r}))\n"
        + f"Path({str(download_started)!r}).touch()\n",
        encoding="utf-8",
    )
    (scripts / "split_episode.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "folder = Path(sys.argv[sys.argv.index('-o') + 1])\n"
        "for index in (1, 2): (folder / f'941_{index}.mp4').write_bytes(b'split')\n",
        encoding="utf-8",
    )
    (scripts / "upscale_tool.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "assert [Path(p).name for p in sys.argv[1:3]] == ['941_1.mp4', '941_2.mp4']\n"
        "folder = Path(sys.argv[sys.argv.index('--output-dir') + 1])\n"
        "for index in (1, 2): (folder / f'941_{index}_4K.mp4').write_bytes(b'upscaled')\n",
        encoding="utf-8",
    )
    assert pipeline.latest_materials() == 0
    assert fetch_finished.is_file()
    assert (materials / "941_1_4K.mp4").read_bytes() == b"upscaled"
    assert (materials / "941_2_4K.mp4").read_bytes() == b"upscaled"
    assert "covers finished" in (materials / "materials_log.txt").read_text(encoding="utf-8")
    state = tmp_path / "logs/.last_doraemon_materials"
    assert state.read_text(encoding="utf-8").strip() == str(materials.resolve())
    pending = tmp_path / "logs" / pipeline.PENDING_MARKER
    assert pending.read_text(encoding="utf-8").strip() == str(materials.resolve())
    assert set(state.parent.iterdir()) == {state, pending}
    saved = json.loads((materials / pipeline.STATE_NAME).read_text(encoding="utf-8"))
    assert saved["status"] == "completed"
    assert saved["stage"] == "done"


@pytest.mark.parametrize("video_rc", [0, 7])
def test_fetch_failure_does_not_change_video_result(monkeypatch, materials, video_rc, capsys):
    proc = Mock()
    proc.wait.return_value = 3
    start = Mock(return_value=proc)
    monkeypatch.setattr(pipeline, "LoggedProcess", start)
    video = Mock(return_value=video_rc)
    monkeypatch.setattr(pipeline, "_video_pipeline", video)
    assert pipeline.latest_materials() == video_rc
    video.assert_called_once_with("941", "2026-11-07", materials.resolve())
    proc.wait.assert_called_once_with()
    assert "Materials fetch failed" in capsys.readouterr().out
    assert start.call_args.args[1].path == materials / "materials_log.txt"


@pytest.mark.parametrize("video_rc", [0, 7])
def test_log_write_failure_does_not_block_video_or_console_fetch(monkeypatch, materials, video_rc, capsys):
    materials.mkdir(parents=True)
    (materials / "materials_log.txt").mkdir()
    video = Mock(return_value=video_rc)
    monkeypatch.setattr(pipeline, "_video_pipeline", video)
    proc = Mock()
    proc.wait.return_value = 0

    def start(command, logger, **kwargs):
        logger.event("materials progress is visible")
        return proc

    monkeypatch.setattr(pipeline, "LoggedProcess", start)
    assert pipeline.latest_materials() == video_rc
    video.assert_called_once_with("941", "2026-11-07", materials.resolve())
    proc.wait.assert_called_once_with()
    output = capsys.readouterr().out
    assert "Cannot write log" in output
    assert "materials progress is visible" in output


def test_fetch_start_failure_does_not_block_video(monkeypatch, materials, capsys):
    monkeypatch.setattr(pipeline, "LoggedProcess", Mock(side_effect=OSError("cannot start")))
    video = Mock(return_value=0)
    monkeypatch.setattr(pipeline, "_video_pipeline", video)
    assert pipeline.latest_materials() == 0
    video.assert_called_once()
    assert "Cannot start materials fetch" in capsys.readouterr().out
    saved = json.loads((materials / pipeline.STATE_NAME).read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["stage"] == "materials"


def test_video_exception_stops_fetch(monkeypatch, materials):
    proc = Mock()
    monkeypatch.setattr(pipeline, "LoggedProcess", Mock(return_value=proc))
    monkeypatch.setattr(pipeline, "_video_pipeline", Mock(side_effect=OSError("video start failed")))
    assert pipeline.main([]) == 1
    proc.stop.assert_called_once_with()
    proc.wait.assert_not_called()


def test_materials_only_mode_is_synchronous_and_keeps_existing_videos(monkeypatch, materials):
    materials.mkdir(parents=True)
    existing = materials / "941.mp4"
    existing.write_bytes(b"existing video")
    run = Mock(return_value=4)
    monkeypatch.setattr(pipeline, "_run_script", run)
    start = Mock()
    monkeypatch.setattr(pipeline, "LoggedProcess", start)
    assert pipeline.latest_materials(download_video=False) == 4
    run.assert_called_once_with("fetch_doraemon.py", "941", log_file=materials / "materials_log.txt")
    start.assert_not_called()
    assert existing.read_bytes() == b"existing video"


@pytest.mark.parametrize(("download_rc", "split_rc", "expected", "calls"), [(5, 0, 5, 1), (0, 6, 6, 2), (0, 2, 0, 2)])
def test_video_failure_or_split_cancel_stops_upscale(tmp_path, monkeypatch, download_rc, split_rc, expected, calls):
    run = Mock(side_effect=[download_rc, split_rc])
    monkeypatch.setattr(pipeline, "_run_script", run)
    assert pipeline._video_pipeline("941", "2026-11-07", tmp_path) == expected
    assert run.call_count == calls
    assert run.call_args_list[0].args[-2:] == ("--broadcast-date", "2026-11-07")


def test_video_pipeline_requires_both_story_clips(tmp_path, monkeypatch):
    (tmp_path / "941_1.mp4").write_bytes(b"clip")
    run = Mock(return_value=0)
    monkeypatch.setattr(pipeline, "_run_script", run)
    assert pipeline._video_pipeline("941", "2026-11-07", tmp_path) == 1
    assert run.call_count == 2


def test_no_latest_episode_starts_neither_branch(monkeypatch, materials):
    monkeypatch.setattr(pipeline.fetch_doraemon, "_latest_item", lambda: None)
    start = Mock()
    monkeypatch.setattr(pipeline, "LoggedProcess", start)
    assert pipeline.latest_materials() == 1
    start.assert_not_called()
    assert not materials.exists()


def test_run_script_uses_argument_list_for_paths_with_spaces(monkeypatch, tmp_path):
    run = Mock(return_value=0)
    monkeypatch.setattr(pipeline, "run_logged", run)
    python = tmp_path / "upscale env/python.exe"
    assert pipeline._run_script("upscale_tool.py", "some video.mp4", "--output-dir", "素材 dir !", python=python) == 0
    assert run.call_args.args[0] == [
        str(python),
        "-u",
        str(pipeline.ROOT / "scripts/upscale_tool.py"),
        "some video.mp4",
        "--output-dir",
        "素材 dir !",
    ]
    assert run.call_args.kwargs == {"cwd": pipeline.ROOT, "interactive": False}
