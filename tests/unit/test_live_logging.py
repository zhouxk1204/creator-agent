from __future__ import annotations

import os
import re
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
    process.quiet = False
    process.proc = Mock()
    process.proc.wait.side_effect = [subprocess.TimeoutExpired("local", 10), 0]
    process.reader = Mock()
    progress = Mock(return_value="1/2 frames")
    monkeypatch.setattr(logging.time, "monotonic", lambda: 10)
    assert process.wait(progress=progress) == 0
    assert progress.call_count == 3
    process.reader.join.assert_called_once_with()
    assert process.logger.status.call_count == 3
    assert "1/2 frames" in process.logger.status.call_args.args[0]
    process.logger.finish_status.assert_called_once_with()
    assert "[END]" in process.logger.event.call_args.args[0]


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


@pytest.fixture(autouse=True)
def clean_console_state():
    logging._CONSOLE_OWNER = None
    logging._CONSOLE_LINE_START = True
    logging._STATUS_OWNER = None
    logging._STATUS_TEXT = ""
    logging._STATUS_WIDTH = 0
    yield
    logging._CONSOLE_OWNER = None
    logging._CONSOLE_LINE_START = True
    logging._STATUS_OWNER = None
    logging._STATUS_TEXT = ""
    logging._STATUS_WIDTH = 0


def visible_lines(output):
    """Small ASCII terminal model: CR overwrites, LF commits the current row."""
    rows, row, column = [], [], 0
    for char in output:
        if char == "\r":
            column = 0
        elif char == "\n":
            rows.append("".join(row).rstrip())
            row, column = [], 0
        else:
            if column < len(row):
                row[column] = char
            else:
                row.append(char)
            column += 1
    if row:
        rows.append("".join(row).rstrip())
    return [row for row in rows if row]


@pytest.mark.parametrize(
    ("tty", "setting", "live", "expected"),
    [
        (True, None, None, True),
        (False, None, None, False),
        (False, "0", None, False),
        (False, "1", None, True),
        (True, "0", None, True),
        (False, "1", False, False),
        (False, None, True, True),
    ],
)
def test_live_progress_selection_respects_launcher_opt_in(monkeypatch, capsys, tty, setting, live, expected):
    monkeypatch.setattr(logging.sys.stdout, "isatty", lambda: tty)
    if setting is None:
        monkeypatch.delenv("CREATOR_AGENT_LIVE_PROGRESS", raising=False)
    else:
        monkeypatch.setenv("CREATOR_AGENT_LIVE_PROGRESS", setting)
    logger = logging.LiveLog(None, "upscale", live=live)
    assert logger.live is expected
    logger.status("[INFERENCE] [########--------] 1/2 frames")
    output = capsys.readouterr().out
    assert ("\r" in output) is expected
    logger.finish_status(record=False)


def test_status_overwrites_console_but_only_final_summary_is_logged(tmp_path, capsys):
    path = tmp_path / "run.txt"
    logger = logging.LiveLog(path, "upscale", live=True)
    logger.status("[INFERENCE] [----------------] 0/123451 frames")
    logger.status("[INFERENCE] [########--------] 60000/123451 frames")
    assert not path.exists()
    logger.finish_status()
    text = path.read_text(encoding="utf-8")
    assert "60000/123451" in text and " 0/123451 " not in text
    assert text.count("[PROGRESS DONE]") == 1
    assert "\r" not in text
    rows = visible_lines(capsys.readouterr().out)
    assert len(rows) == 1
    assert "60000/123451" in rows[0]


def test_parallel_message_clears_then_redraws_the_progress_line(tmp_path, capsys):
    video = logging.LiveLog(tmp_path / "video.txt", "video", live=True)
    materials = logging.LiveLog(tmp_path / "materials.txt", "materials", live=True)
    video.status("[INFERENCE] 1/20 frames")
    materials.event("cover ready")
    video.status("[INFERENCE] 2/20 frames")
    video.finish_status()
    rows = visible_lines(capsys.readouterr().out)
    assert len(rows) == 2
    assert rows[0].endswith("[materials] cover ready")
    assert rows[1].endswith("[PROGRESS DONE] [INFERENCE] 2/20 frames")
    assert "[INFERENCE]" not in materials.path.read_text(encoding="utf-8")


def test_redirected_output_only_contains_final_progress_summary(tmp_path, capsys):
    logger = logging.LiveLog(tmp_path / "run.txt", "video", live=False)
    for index in range(100):
        logger.status(f"[INFERENCE] {index}/100 frames")
    assert capsys.readouterr().out == ""
    logger.finish_status()
    output = capsys.readouterr().out
    assert "99/100 frames" in output
    assert output.count("\n") == 1
    assert "\r" not in output


@pytest.mark.parametrize("separator", ["\r", "\n", "\r\n"])
def test_real_child_progress_refreshes_are_not_durable_log_lines(tmp_path, capsys, separator):
    logger = logging.LiveLog(tmp_path / "run.txt", "download", live=True)
    code = (
        "import sys\n"
        "for value in (10, 50, 100): "
        "sys.stdout.write('[DOWNLOAD] %s%% at 2.0MiB/s 10MiB / 20MiB ETA 5s' % value"
        f" + {separator!r}); sys.stdout.flush()\n"
        "print('WARNING: diagnostic retained', file=sys.stderr)\n"
    )
    assert logging.run_logged([sys.executable, "-u", "-c", code], logger, quiet=True) == 0
    text = logger.path.read_text(encoding="utf-8")
    assert text.count("[PROGRESS DONE]") == 1
    assert "[DOWNLOAD] [" in text
    assert "100%" in text and "2.0MiB/s" in text
    assert "10%" not in text and "50%" not in text
    assert "WARNING: diagnostic retained" in text
    rows = visible_lines(capsys.readouterr().out)
    assert len(rows) == 2
    assert "diagnostic retained" in rows[0]
    assert "100%" in rows[1]


@pytest.mark.parametrize("live", [True, None])
def test_nested_wrapper_preserves_live_frames_without_duplicate_final_records(tmp_path, capsys, monkeypatch, live):
    monkeypatch.setenv("CREATOR_AGENT_LIVE_PROGRESS", "1")
    scripts = Path(logging.__file__).parent
    child_code = "import sys; [print('[INFERENCE] %s/3 frames' % i, end='\\r', flush=True) for i in range(4)]"
    wrapper_code = (
        f"import sys; sys.path.insert(0, {str(scripts)!r})\n"
        "from live_logging import LiveLog, run_logged\n"
        f"raise SystemExit(run_logged([sys.executable, '-u', '-c', {child_code!r}], "
        "LiveLog(None, 'inner'), quiet=True))\n"
    )
    logger = logging.LiveLog(tmp_path / "pipeline.txt", "outer", live=live)
    assert logging.run_logged([sys.executable, "-u", "-c", wrapper_code], logger, quiet=True) == 0
    text = logger.path.read_text(encoding="utf-8")
    assert text.count("[PROGRESS DONE]") == 1
    assert "3/3 frames" in text
    assert "0/3 frames" not in text
    assert len(visible_lines(capsys.readouterr().out)) == 1


def test_process_without_callback_does_not_log_running_heartbeats():
    process = logging.LoggedProcess.__new__(logging.LoggedProcess)
    process.logger = Mock()
    process.started = time.monotonic()
    process.quiet = False
    process.proc = Mock()
    process.proc.wait.side_effect = [subprocess.TimeoutExpired("local", 1), 0]
    process.reader = Mock()
    assert process.wait() == 0
    process.logger.status.assert_not_called()
    assert process.logger.event.call_count == 1
    assert "[END]" in process.logger.event.call_args.args[0]


def test_progress_line_never_wraps_a_narrow_terminal(monkeypatch, capsys):
    monkeypatch.setattr(logging.shutil, "get_terminal_size", lambda **kwargs: os.terminal_size((80, 24)))
    logger = logging.LiveLog(None, "download", live=True)
    logger.status("[DOWNLOAD] 50% at 2MiB/s " + "details " * 20)
    output = capsys.readouterr().out
    assert "2MiB/s" in output
    assert len(output.removeprefix("\r")) < 80
    logger.finish_status(record=False)


@pytest.mark.parametrize("columns", [20, 40, 60, 80, 120])
@pytest.mark.parametrize("done", [0, 60000, 123451])
def test_inference_bar_keeps_frame_count_on_its_right_in_narrow_terminals(tmp_path, monkeypatch, capsys, columns, done):
    monkeypatch.setattr(logging.shutil, "get_terminal_size", lambda **kwargs: os.terminal_size((columns, 24)))
    logger = logging.LiveLog(tmp_path / "run.txt", "upscale_tool", live=True)
    status = f"[INFERENCE] {logging.progress_bar(done, 123451)} {done}/123451 frames (48.6%); 1.50 frames/s; ETA=42301s"
    logger.status(status)
    rendered = capsys.readouterr().out.removeprefix("\r")
    assert len(rendered) < columns
    assert "\n" not in rendered and "..." not in rendered
    assert re.search(rf"\[[#?\-]+\] {done}/123451\b", rendered)
    if columns >= 40:
        assert f"{done}/123451 frames" in rendered
    if 0 < done < 123451:
        assert "#" in rendered and "-" in rendered
    logger.finish_status()
    assert status in logger.path.read_text(encoding="utf-8")


def test_child_filter_hides_per_image_logs_but_keeps_errors(tmp_path):
    code = "print('Testing 0 frame_000001'); print('Testing 1 frame_000002'); print('RuntimeError: GPU failed')"
    logger = logging.LiveLog(tmp_path / "run.txt", "upscale", live=False)
    assert (
        logging.run_logged(
            [sys.executable, "-c", code],
            logger,
            quiet=True,
            output_filter=lambda line: not line.startswith("Testing "),
        )
        == 0
    )
    text = logger.path.read_text(encoding="utf-8")
    assert "[upscale] Testing" not in text
    assert "RuntimeError: GPU failed" in text


def test_diagnostics_with_a_progress_marker_are_not_overwritten(tmp_path):
    code = (
        "print('[INFERENCE] 1/10 frames'); "
        "print('RuntimeError: [INFERENCE] GPU failed'); "
        "print('[INFERENCE] 2/10 frames')"
    )
    logger = logging.LiveLog(tmp_path / "run.txt", "upscale", live=False)
    assert logging.run_logged([sys.executable, "-c", code], logger, quiet=True) == 0
    text = logger.path.read_text(encoding="utf-8")
    assert "[upscale] RuntimeError: [INFERENCE] GPU failed" in text
    assert text.count("[PROGRESS DONE]") == 1
    assert "2/10 frames" in text
