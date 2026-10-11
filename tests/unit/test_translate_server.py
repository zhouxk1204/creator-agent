"""Unit tests for creator_agent.translate.server (lazy llama-server management)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from creator_agent.translate.server import _root_url, ensure_server, server_ready


def test_root_url_strips_v1():
    assert _root_url("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080"
    assert _root_url("http://127.0.0.1:8080/v1/") == "http://127.0.0.1:8080"
    # Servers rooted elsewhere keep their path.
    assert _root_url("http://host:11434") == "http://host:11434"


class _Handler(BaseHTTPRequestHandler):
    health_status = 200
    models_status = 200

    def do_GET(self):
        if self.path == "/health":
            code = type(self).health_status
        elif self.path == "/v1/models":
            code = type(self).models_status
        else:
            code = 404
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):  # keep tests quiet
        pass


@pytest.fixture()
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    thread.join()


def test_server_ready_when_health_200(http_server):
    _Handler.health_status = 200
    assert server_ready(http_server) is True


def test_server_not_ready_while_model_loads(http_server):
    # llama-server answers 503 on /health until the model is loaded.
    _Handler.health_status = 503
    assert server_ready(http_server) is False


def test_server_ready_falls_back_to_models_when_no_health(http_server):
    # e.g. Ollama: no /health endpoint, but /v1/models answers 200.
    _Handler.health_status = 404
    _Handler.models_status = 200
    assert server_ready(http_server) is True


def test_server_not_ready_when_connection_refused():
    assert server_ready("http://127.0.0.1:1/v1", timeout=0.5) is False


def test_ensure_server_returns_none_when_already_running(http_server):
    _Handler.health_status = 200
    assert ensure_server(http_server, echo=lambda *_: None) is None
