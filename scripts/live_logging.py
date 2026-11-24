"""Timestamped console/file output for long-running media subprocesses."""

from __future__ import annotations

import codecs
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

_OUTPUT_LOCK = threading.RLock()
_CONSOLE_OWNER = None
_CONSOLE_LINE_START = True
_STATUS_OWNER = None
_STATUS_TEXT = ""
_STATUS_WIDTH = 0
_PROGRESS_LINE = re.compile(r"\[(?:DOWNLOAD|EXTRACT|INFERENCE|ENCODE)\].*")


def progress_bar(done: float, total: float | None, width: int = 16) -> str:
    if not total or total <= 0:
        return "[" + "?" + " " * (width - 1) + "]"
    filled = int(max(0, min(done / total, 1)) * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _clear_status() -> None:
    global _STATUS_WIDTH
    if _STATUS_WIDTH:
        print("\r" + " " * _STATUS_WIDTH + "\r", end="", flush=True)
        _STATUS_WIDTH = 0


def _redraw_status() -> None:
    global _STATUS_WIDTH
    if _STATUS_OWNER is not None and _STATUS_OWNER.live:
        columns = max(20, shutil.get_terminal_size(fallback=(120, 24)).columns)
        rendered = _STATUS_TEXT
        if len(rendered) >= columns:
            rendered = rendered[: columns - 4] + "..."
        print("\r" + rendered, end="", flush=True)
        _STATUS_WIDTH = len(rendered)


def install_interrupt_handler() -> None:
    """Let wrapper processes unwind and stop their nested process groups."""
    termination_signal = getattr(signal, "SIGBREAK", signal.SIGTERM)
    signal.signal(termination_signal, signal.default_int_handler)


def _timestamped(text: str, label: str, line_start: bool) -> tuple[str, bool]:
    chunks = []
    for line in text.splitlines(keepends=True):
        if line_start:
            chunks.append(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{label}] ")
        chunks.append(line)
        line_start = line.endswith("\n")
    return "".join(chunks), line_start


class LiveLog:
    def __init__(self, path: Path | None, label: str, *, live: bool | None = None):
        self.path = path
        self.label = label
        self._line_start = True
        self._status: str | None = None
        self.live = (
            bool(getattr(sys.stdout, "isatty", lambda: False)()) or os.environ.get("CREATOR_AGENT_LIVE_PROGRESS") == "1"
            if live is None
            else live
        )

    def status(self, message: str) -> None:
        """Overwrite one console line without appending every refresh to disk.

        Nested wrappers opt their child pipes into carriage-return updates;
        redirected top-level output keeps only the final progress summary.
        """
        global _STATUS_OWNER, _STATUS_TEXT, _CONSOLE_OWNER, _CONSOLE_LINE_START
        message = message.strip()
        if message.startswith("[DOWNLOAD]") and not message.startswith("[DOWNLOAD] ["):
            percent = re.search(r"(\d+(?:\.\d+)?)%", message)
            bar = progress_bar(float(percent[1]) if percent else 0, 100 if percent else None)
            message = message.replace("[DOWNLOAD]", f"[DOWNLOAD] {bar}", 1)
        with _OUTPUT_LOCK:
            _clear_status()
            if not _CONSOLE_LINE_START:
                print(flush=True)
                _CONSOLE_LINE_START = True
                _CONSOLE_OWNER = None
            self._status = message
            _STATUS_OWNER = self
            _STATUS_TEXT = f"[{self.label}] {message}"
            _redraw_status()

    def finish_status(self, *, record: bool = True) -> None:
        global _STATUS_OWNER, _STATUS_TEXT
        with _OUTPUT_LOCK:
            message, self._status = self._status, None
            if _STATUS_OWNER is self:
                _clear_status()
                _STATUS_OWNER = None
                _STATUS_TEXT = ""
            if message is not None and record:
                self.event(f"[PROGRESS DONE] {message}")

    def output(self, text: str) -> None:
        global _CONSOLE_OWNER, _CONSOLE_LINE_START
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        with _OUTPUT_LOCK:
            _clear_status()
            rendered, self._line_start = _timestamped(text, self.label, self._line_start)
            # Console boundaries are shared, file boundaries remain per stream.
            if _CONSOLE_OWNER is not self and not _CONSOLE_LINE_START:
                print(flush=True)
                _CONSOLE_LINE_START = True
            console, _CONSOLE_LINE_START = _timestamped(text, self.label, _CONSOLE_LINE_START)
            print(console, end="", flush=True)
            _CONSOLE_OWNER = self if not _CONSOLE_LINE_START else None
            if self.path is not None:
                try:
                    with self.path.open("a", encoding="utf-8") as handle:
                        handle.write(rendered)
                except OSError as exc:
                    print(f"\n[WARNING] Cannot write log {self.path}: {exc}; console logging continues.", flush=True)
                    self.path = None
                    _CONSOLE_LINE_START = True
                    _CONSOLE_OWNER = None
            if _CONSOLE_LINE_START:
                _redraw_status()

    def event(self, message: str, *, console: bool = True) -> None:
        with _OUTPUT_LOCK:
            if console:
                if not self._line_start:
                    self.output("\n")
                self.output(message + "\n")
            elif self.path is not None:
                rendered, self._line_start = _timestamped(message + "\n", self.label, self._line_start)
                try:
                    with self.path.open("a", encoding="utf-8") as handle:
                        handle.write(rendered)
                except OSError as exc:
                    self.output(f"[WARNING] Cannot write log {self.path}: {exc}; console logging continues.\n")
                    self.path = None


class LoggedProcess:
    def __init__(
        self,
        command: list[str],
        logger: LiveLog,
        *,
        cwd: Path | None = None,
        interactive: bool = False,
        quiet: bool = False,
        output_filter: Callable[[str], bool] | None = None,
    ):
        self.logger = logger
        self.quiet = quiet
        self.interactive = interactive
        self.output_filter = output_filter
        self.own_group = not interactive or os.name == "nt"
        self.started = time.monotonic()
        logger.event(f"[START] {subprocess.list2cmdline(command)}", console=not quiet)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "CREATOR_AGENT_LIVE_PROGRESS": "1"}
        self.proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=os.name != "nt" and self.own_group,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        logger.event(f"[PROCESS] PID={self.proc.pid}", console=not quiet)

    def _read(self) -> None:
        assert self.proc.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        previous_separator = ""

        def forward(line: str, separator: str) -> None:
            if self.output_filter is not None and not self.output_filter(line):
                return
            if not line.strip():
                if separator == "\n" and previous_separator != "\r":
                    self.logger.output(line + "\n")
                return
            if "[PROGRESS DONE]" in line:
                # A nested wrapper already committed the final status once.
                self.logger.finish_status(record=False)
                self.logger.output(line + "\n")
            elif re.search(r"warning|error|traceback|exception", line, re.IGNORECASE):
                self.logger.output(line + "\n")
            elif match := _PROGRESS_LINE.search(line):
                self.logger.status(match[0])
            elif separator == "\r":
                self.logger.status(line)
            else:
                self.logger.output(line + "\n")

        try:
            while chunk := self.proc.stdout.read(4096):
                pending += decoder.decode(chunk)
                while match := re.search(r"[\r\n]", pending):
                    line, separator = pending[: match.start()], match[0]
                    pending = pending[match.end() :]
                    forward(line, separator)
                    previous_separator = separator
                if self.interactive and pending:
                    # Split confirmation prompts do not end in a newline.
                    self.logger.output(pending)
                    pending = ""
            pending += decoder.decode(b"", final=True)
            if pending:
                forward(pending, "\n")
        except OSError as exc:
            self.logger.event(f"[WARNING] Cannot read process output: {exc}")
        finally:
            self.proc.stdout.close()

    def poll(self) -> int | None:
        return self.proc.poll()

    def wait(self, *, interval: float = 1, progress: Callable[[], str] | None = None) -> int:
        if progress is not None:
            self.logger.status(progress())
        while True:
            try:
                rc = self.proc.wait(timeout=interval)
                break
            except subprocess.TimeoutExpired:
                if progress is not None:
                    self.logger.status(progress())
        self.reader.join()
        if progress is not None:
            self.logger.status(progress())
        self.logger.finish_status()
        self.logger.event(f"[END] exit={rc}; elapsed={time.monotonic() - self.started:.1f}s", console=not self.quiet)
        return rc

    def stop(self) -> None:
        self.logger.finish_status()
        if self.proc.poll() is not None:
            self.reader.join()
            self.logger.finish_status()
            return
        self.logger.event(f"[INTERRUPTED] Stopping process group PID={self.proc.pid}")
        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            elif self.own_group:
                os.killpg(self.proc.pid, signal.SIGTERM)
            else:
                self.proc.terminate()
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            elif self.own_group:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                self.proc.kill()
            self.proc.wait()
        self.reader.join()
        self.logger.finish_status()


def run_logged(
    command: list[str],
    logger: LiveLog,
    *,
    cwd: Path | None = None,
    progress: Callable[[], str] | None = None,
    interactive: bool = False,
    quiet: bool = False,
    output_filter: Callable[[str], bool] | None = None,
) -> int:
    process = LoggedProcess(command, logger, cwd=cwd, interactive=interactive, quiet=quiet, output_filter=output_filter)
    try:
        return process.wait(progress=progress)
    except BaseException:
        process.stop()
        raise
