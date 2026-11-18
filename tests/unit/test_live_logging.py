from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import live_logging as logging  # noqa: E402


def test_log_appends_timestamped_output_and_normalizes_progress(tmp_path, capsys):
    path = tmp_path / "log.txt"
    path.write_text("previous run\n", encoding="utf-8")
    logger = logging.LiveLog(path, "test")
    logger.output("partial")
    logger.output(" line\rnext\r\n")
    logger.event("finished")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("previous run\n")
    assert "[test] partial line\n" in text
    assert "[test] next\n" in text
    assert "[test] finished\n" in text
    assert "\r" not in text
    assert capsys.readouterr().out == text.removeprefix("previous run\n")


def test_log_file_failure_keeps_console_output(tmp_path, capsys):
    logger = logging.LiveLog(tmp_path / "missing" / "log.txt", "test")
    logger.event("first")
    logger.event("second")
    output = capsys.readouterr().out
    assert "[test] first" in output
    assert "[test] second" in output
    assert output.count("Cannot write log") == 1
    assert logger.path is None


def test_real_local_process_streams_stdout_stderr_and_preserves_log(tmp_path):
    path = tmp_path / "日志 !.txt"
    path.write_text("previous run\n", encoding="utf-8")
    command = [sys.executable, "-u", "-c", "import sys; print('素材'); print('stderr', file=sys.stderr)"]
    assert logging.run_logged(command, logging.LiveLog(path, "child"), cwd=tmp_path) == 0
    text = path.read_text(encoding="utf-8")
    assert text.startswith("previous run\n")
    assert "[child] 素材" in text
    assert "[child] stderr" in text
    assert "[END] exit=0" in text


def test_real_local_process_nonzero_exit_is_returned(tmp_path):
    assert logging.run_logged([sys.executable, "-c", "raise SystemExit(7)"], logging.LiveLog(None, "child")) == 7


def test_process_wait_reports_progress(monkeypatch):
    process = logging.LoggedProcess.__new__(logging.LoggedProcess)
    process.logger = Mock()
    process.started = 0
    process.proc = Mock()
    process.proc.wait.side_effect = [subprocess.TimeoutExpired("local", 10), 0]
    process.reader = Mock()
    progress = Mock(return_value="1/2 frames")
    monkeypatch.setattr(logging.time, "monotonic", lambda: 10)
    assert process.wait(progress=progress) == 0
    progress.assert_called_once_with()
    process.reader.join.assert_called_once_with()
    assert "1/2 frames" in process.logger.event.call_args_list[0].args[0]


@pytest.mark.parametrize("error", [KeyboardInterrupt(), RuntimeError("progress failed")])
def test_run_logged_stops_child_before_propagating_error(monkeypatch, error):
    process = Mock()
    process.wait.side_effect = error
    monkeypatch.setattr(logging, "LoggedProcess", Mock(return_value=process))
    with pytest.raises(type(error)):
        logging.run_logged(["mock command"], logging.LiveLog(None, "test"))
    process.stop.assert_called_once_with()


def test_stop_terminates_a_local_child_and_joins_reader(tmp_path):
    process = logging.LoggedProcess(
        [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(60)"],
        logging.LiveLog(tmp_path / "log.txt", "local"),
    )
    try:
        process.stop()
        assert process.poll() is not None
        assert not process.reader.is_alive()
    finally:
        if process.poll() is None:
            process.proc.kill()
            process.proc.wait()


def test_parallel_partial_lines_are_separated_only_in_shared_console(tmp_path, capsys):
    first = logging.LiveLog(tmp_path / "first.txt", "first")
    second = logging.LiveLog(tmp_path / "second.txt", "second")
    first.output("long line")
    second.event("parallel progress")
    first.output(" continued\n")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert lines[0].endswith("[first] long line")
    assert lines[1].endswith("[second] parallel progress")
    assert lines[2].endswith("[first]  continued")
    assert "[first] long line continued\n" in first.path.read_text(encoding="utf-8")
    assert "[second] parallel progress\n" in second.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("windows", [False, True])
def test_wrapper_termination_signal_unwinds_as_keyboard_interrupt(monkeypatch, windows):
    register = Mock()
    fake_signal = SimpleNamespace(signal=register, SIGTERM=15, default_int_handler=signal.default_int_handler)
    if windows:
        fake_signal.SIGBREAK = 21
    monkeypatch.setattr(logging, "signal", fake_signal)
    logging.install_interrupt_handler()
    register.assert_called_once_with(21 if windows else 15, signal.default_int_handler)
    with pytest.raises(KeyboardInterrupt):
        register.call_args.args[1](register.call_args.args[0], None)


@pytest.mark.skipif(os.name == "nt", reason="POSIX real-process proof; Windows signals are mocked above")
def test_stopping_wrapper_also_stops_its_separate_child_group(tmp_path):
    ready = tmp_path / "nested.pid"
    scripts = Path(logging.__file__).parent
    code = (
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(scripts)!r})\n"
        "from live_logging import LiveLog, LoggedProcess, install_interrupt_handler\n"
        "install_interrupt_handler()\n"
        "child = LoggedProcess([sys.executable, '-u', '-c', 'import time; time.sleep(60)'], LiveLog(None, 'nested'))\n"
        "try:\n"
        f"    Path({str(ready)!r}).write_text(str(child.proc.pid))\n"
        "    child.wait()\n"
        "except KeyboardInterrupt:\n"
        "    child.stop()\n"
        "    raise SystemExit(130)\n"
    )
    wrapper = logging.LoggedProcess([sys.executable, "-u", "-c", code], logging.LiveLog(None, "wrapper"))
    nested_pid = None
    try:
        deadline = time.monotonic() + 10
        while not ready.is_file():
            assert time.monotonic() < deadline, "local child did not start"
            assert wrapper.poll() is None, "local wrapper exited early"
            time.sleep(0.01)
        nested_pid = int(ready.read_text())
        wrapper.stop()
        assert wrapper.poll() == 130
        assert not wrapper.reader.is_alive()
        with pytest.raises(ProcessLookupError):
            os.kill(nested_pid, 0)
    finally:
        if wrapper.poll() is None:
            wrapper.stop()
        if nested_pid is not None:
            try:
                os.kill(nested_pid, 0)
            except ProcessLookupError:
                pass
            else:
                os.kill(nested_pid, signal.SIGKILL)
