"""Timestamped console/file output for long-running media subprocesses."""

from __future__ import annotations

import codecs
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

_OUTPUT_LOCK = threading.RLock()
_CONSOLE_OWNER = None
_CONSOLE_LINE_START = True


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
    def __init__(self, path: Path | None, label: str):
        self.path = path
        self.label = label
        self._line_start = True

    def output(self, text: str) -> None:
        global _CONSOLE_OWNER, _CONSOLE_LINE_START
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        with _OUTPUT_LOCK:
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

    def event(self, message: str) -> None:
        with _OUTPUT_LOCK:
            if not self._line_start:
                self.output("\n")
            self.output(message + "\n")


class LoggedProcess:
    def __init__(self, command: list[str], logger: LiveLog, *, cwd: Path | None = None, interactive: bool = False):
        self.logger = logger
        self.own_group = not interactive or os.name == "nt"
        self.started = time.monotonic()
        logger.event(f"[START] {subprocess.list2cmdline(command)}")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
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
        logger.event(f"[PROCESS] PID={self.proc.pid}")

    def _read(self) -> None:
        assert self.proc.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while chunk := self.proc.stdout.read(4096):
                self.logger.output(decoder.decode(chunk))
            self.logger.output(decoder.decode(b"", final=True))
        except OSError as exc:
            self.logger.event(f"[WARNING] Cannot read process output: {exc}")
        finally:
            self.proc.stdout.close()

    def poll(self) -> int | None:
        return self.proc.poll()

    def wait(self, *, interval: float = 10, progress: Callable[[], str] | None = None) -> int:
        while True:
            try:
                rc = self.proc.wait(timeout=interval)
                break
            except subprocess.TimeoutExpired:
                detail = progress() if progress is not None else "process still running"
                self.logger.event(f"[PROGRESS] elapsed={time.monotonic() - self.started:.1f}s; {detail}")
        self.reader.join()
        self.logger.event(f"[END] exit={rc}; elapsed={time.monotonic() - self.started:.1f}s")
        return rc

    def stop(self) -> None:
        if self.proc.poll() is not None:
            self.reader.join()
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


def run_logged(
    command: list[str],
    logger: LiveLog,
    *,
    cwd: Path | None = None,
    progress: Callable[[], str] | None = None,
    interactive: bool = False,
) -> int:
    process = LoggedProcess(command, logger, cwd=cwd, interactive=interactive)
    try:
        return process.wait(progress=progress)
    except BaseException:
        process.stop()
        raise
