"""Lazy management of the local translation LLM server (llama.cpp).

The server is only needed for the translate phase, so it is started right
before translation and stopped right after — never during ASR. Running it
concurrently with the ASR worker (torch + CUDA + vocal separator + the ASR
model) pushes past the Windows commit limit and the ASR model load dies with
os error 1455 ("page file too small").

Same launch parameters as bat/start_translate_server.bat; the LLAMA_SERVER /
TRANSLATE_MODEL / TRANSLATE_ALIAS env vars override the defaults.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

_HEALTH_TIMEOUT = 3.0
_START_TIMEOUT = 180.0  # loading a 9B GGUF into VRAM can take a minute or two

_DEFAULT_EXE = r"C:\tools\llama.cpp\llama-server.exe"
_DEFAULT_MODEL = r"C:\models\Qwen3.5-9B-Q6_K.gguf"
_DEFAULT_ALIAS = "qwen3.5-9b"

_SW_MINIMIZE = 6


def _root_url(base_url: str) -> str:
    # base_url is the OpenAI-compatible endpoint (.../v1); llama-server's
    # /health lives at the server root.
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def server_ready(base_url: str, timeout: float = _HEALTH_TIMEOUT) -> bool:
    """True only when the server can actually serve inference.

    llama-server answers /v1/models the moment its HTTP listener is up but
    returns 503 on /health until the model is loaded, so probe /health first.
    Servers without /health (e.g. Ollama) fall back to the models listing.
    """
    try:
        with urllib.request.urlopen(_root_url(base_url) + "/health", timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return False  # 503 = model still loading; anything else = not ready
    except (urllib.error.URLError, OSError):
        return False
    # No /health endpoint: a 200 on the models listing counts as ready.
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def ensure_server(base_url: str, echo=print) -> subprocess.Popen | None:
    """Return a ready translation server, starting llama-server if needed.

    Returns the Popen handle when WE started it (the caller must stop_server()
    it when done), or None when a server was already running (left untouched).
    """
    if server_ready(base_url):
        echo("[server] translation LLM already running.")
        return None
    exe = os.environ.get("LLAMA_SERVER", _DEFAULT_EXE)
    model = os.environ.get("TRANSLATE_MODEL", _DEFAULT_MODEL)
    alias = os.environ.get("TRANSLATE_ALIAS", _DEFAULT_ALIAS)
    if not os.path.exists(exe):
        raise RuntimeError(
            f"translation server is not running at {base_url} and llama-server "
            f"was not found at {exe} (set the LLAMA_SERVER env var to override)."
        )
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8080
    cmd = [
        exe,
        "-m",
        model,
        "--alias",
        alias,
        "--host",
        host,
        "--port",
        str(port),
        "-c",
        "8192",
        "-ngl",
        "99",
    ]
    echo("[server] starting translation LLM (llama.cpp) in a separate window...")
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        kwargs["startupinfo"] = subprocess.STARTUPINFO(
            dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=_SW_MINIMIZE
        )
    proc = subprocess.Popen(cmd, **kwargs)
    deadline = time.monotonic() + _START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"translation server exited during startup (code {proc.returncode}).")
        if server_ready(base_url):
            echo("[server] ready.")
            return proc
        time.sleep(2)
    stop_server(proc)
    raise RuntimeError(f"translation server did not become ready within {_START_TIMEOUT:.0f}s.")


def stop_server(proc: subprocess.Popen) -> None:
    """Stop a server started by ensure_server (no-op if it already exited)."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
