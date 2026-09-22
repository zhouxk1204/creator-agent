"""Local web UI server (stdlib only - no FastAPI/Flask/uvicorn dependency).

Serves a browseable view of collected videos: a list page of cards, a detail
page with a ``<video>`` player + metadata + full transcript, and the media
endpoints (cover image, video stream) the pages reference.

The video stream implements HTTP ``Range`` so the player can seek inside 50 MB
mp4s without loading the whole file - stdlib's ``SimpleHTTPRequestHandler``
can't do this, which is why we roll a small handler.

Path safety: media endpoints take a ``video_id``/``creator_id`` only and resolve
the on-disk path through the repository + FileStorage (the path is built from
DB-sourced, sanitized fields). The URL id never reaches the filesystem, so
traversal is structurally impossible; a defensive ``..`` check returns 404 early.
"""

from __future__ import annotations

import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlparse

from creator_agent.models.video import VideoStatus
from creator_agent.storage.file_storage import FileStorage
from creator_agent.web.templates import render_detail, render_list, render_run

if TYPE_CHECKING:
    from creator_agent.models.creator import Creator
    from creator_agent.models.video import Video
    from creator_agent.repository.sqlite_repo import Repository
    from creator_agent.web.jobs import RunJobManager

logger = logging.getLogger(__name__)

# Routes. IDs are matched greedily up to the next "/" - they're validated below.
_ROUTE_LIST = re.compile(r"^/$")
_ROUTE_DETAIL = re.compile(r"^/video/([^/]+)$")
_ROUTE_COVER = re.compile(r"^/cover/([^/]+)$")
_ROUTE_STREAM = re.compile(r"^/stream/([^/]+)$")
_ROUTE_AVATAR = re.compile(r"^/avatar/([^/]+)$")
_ROUTE_RUN = re.compile(r"^/run$")

_CHUNK = 1 << 16  # 64 KB streaming chunk


def _is_safe_id(raw: str) -> str | None:
    """URL-decode an id and reject anything that could traverse the filesystem.

    Douyin ids look like ``douyin_7662286666366766346``; creator ids may contain
    dots (``douyin_MS4wLjAB...``), so we can't whitelist chars narrowly. Instead
    we reject separators and ``..`` - the actual path is never built from this
    value anyway (it goes through a DB primary-key lookup).
    """
    if raw is None:
        return None
    ident = unquote(raw)
    if "/" in ident or "\\" in ident or ".." in ident:
        return None
    return ident


class WebServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the repo + storage the handler reads from."""

    daemon_threads = True

    def __init__(
        self,
        server_address,
        handler,
        repo: Repository,
        storage: FileStorage,
        jobs: RunJobManager | None = None,
    ) -> None:
        super().__init__(server_address, handler)
        self.repo = repo
        self.storage = storage
        self.jobs = jobs


class WebHandler(BaseHTTPRequestHandler):
    # Quieter logging - the default spams every request to stderr.
    def log_message(self, fmt, *args):  # noqa: A002 - signature fixed by base class
        logger.debug("%s - %s", self.address_string(), fmt % args)

    @property
    def repo(self) -> Repository:
        return self.server.repo  # type: ignore[attr-defined]

    @property
    def storage(self) -> FileStorage:
        return self.server.storage  # type: ignore[attr-defined]

    @property
    def jobs(self) -> RunJobManager | None:
        return self.server.jobs  # type: ignore[attr-defined]

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        path = urlparse(self.path).path
        try:
            if _ROUTE_LIST.match(path):
                self._serve_list()
            elif _ROUTE_RUN.match(path):
                self._serve_run()
            elif m := _ROUTE_DETAIL.match(path):
                self._serve_detail(_is_safe_id(m.group(1)))
            elif m := _ROUTE_COVER.match(path):
                self._serve_media(_is_safe_id(m.group(1)), kind="cover")
            elif m := _ROUTE_STREAM.match(path):
                self._serve_media(_is_safe_id(m.group(1)), kind="video")
            elif m := _ROUTE_AVATAR.match(path):
                self._serve_avatar(_is_safe_id(m.group(1)))
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception:  # never let one request crash the server thread
            logger.exception("web handler error for %s", path)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_HEAD(self) -> None:  # noqa: N802 - players send HEAD before GET
        path = urlparse(self.path).path
        if (m := _ROUTE_COVER.match(path)) and (vid := _is_safe_id(m.group(1))):
            self._serve_media(vid, kind="cover", head_only=True)
        elif (m := _ROUTE_STREAM.match(path)) and (vid := _is_safe_id(m.group(1))):
            self._serve_media(vid, kind="video", head_only=True)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        path = urlparse(self.path).path
        try:
            if _ROUTE_RUN.match(path):
                self._handle_run_post()
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception:
            logger.exception("web POST error for %s", path)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)

    # -- pages --------------------------------------------------------------

    def _handle_run_post(self) -> None:
        """Form submit from the list page: queue the pasted URL, redirect to /run."""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(length, 1 << 16)).decode("utf-8", "replace")
        url = (parse_qs(body).get("url") or [""])[0]

        if not self.jobs:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Run jobs not enabled")
            return
        accepted, message = self.jobs.submit(url)
        if not accepted:
            self._send_html(render_run(None, submit_error=message))
            return
        # 303 so the follow-up GET renders the status page.
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/run")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_run(self) -> None:
        if not self.jobs:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Run jobs not enabled")
            return
        self._send_html(render_run(self.jobs.snapshot()))

    def _serve_list(self) -> None:
        creators = self.repo.list_creators()
        items: list[dict] = []
        for c in creators:
            for v in self.repo.list_videos(c.id):
                items.append(
                    {
                        "video": v,
                        "creator": c,
                        "has_transcript": v.status == VideoStatus.ASR_DONE,
                    }
                )
        items.sort(key=lambda it: it["video"].published_at, reverse=True)
        self._send_html(render_list(items, creators))

    def _serve_detail(self, video_id: str | None) -> None:
        if not video_id:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        video = self.repo.get_video(video_id)
        if not video:
            self.send_error(HTTPStatus.NOT_FOUND, "Video not found")
            return
        creator = self.repo.get_creator(video.creator_id)
        transcript = self.storage.load_transcript(creator, video) if creator else None
        self._send_html(render_detail(video, creator, transcript))

    # -- media --------------------------------------------------------------

    def _resolve_video_paths(self, video_id: str) -> tuple[Video, Creator, Path] | None:
        """Look up a video + creator and return (video, creator, video_folder)."""
        video = self.repo.get_video(video_id)
        if not video:
            return None
        creator = self.repo.get_creator(video.creator_id)
        if not creator:
            return None
        folder = self.storage._video_path(creator, video)  # noqa: SLF001 - storage exposes this path
        return video, creator, folder

    def _serve_media(self, video_id: str | None, kind: str, head_only: bool = False) -> None:
        if not video_id:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        resolved = self._resolve_video_paths(video_id)
        if not resolved:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        video, creator, folder = resolved
        if kind == "cover":
            path = folder / "cover.jpg"
            content_type = "image/jpeg"
        else:
            path = self.storage.video_file_path(creator, video)
            content_type = "video/mp4"
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found on disk")
            return
        self._send_file(path, content_type, head_only=head_only)

    def _serve_avatar(self, creator_id: str | None) -> None:
        if not creator_id:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        creator = self.repo.get_creator(creator_id)
        if not creator:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = self.storage._creator_path(creator) / "avatar.jpg"  # noqa: SLF001
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._send_file(path, "image/jpeg")

    # -- low-level senders --------------------------------------------------

    def _send_html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str, head_only: bool = False) -> None:
        """Serve a file with HTTP Range support (206) or full content (200).

        ``head_only`` sends headers without a body (HEAD request).
        """
        size = path.stat().st_size
        range_header = self.headers.get("Range")

        if range_header:
            start, end = self._parse_range(range_header, size)
            if start is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if not head_only:
                self._stream(path, start, length)
        else:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if not head_only:
                self._stream(path, 0, size)

    @staticmethod
    def _parse_range(range_header: str, size: int) -> tuple[int | None, int]:
        """Parse ``Range: bytes=start-end`` -> (start, end) inclusive, or (None, 0).

        Supports ``bytes=0-1023``, ``bytes=0-`` (open end), ``bytes=500-`` (suffix
        from 500), and ``bytes=-500`` (last 500 bytes). Returns (None, 0) for
        unsatisfiable ranges so the caller can answer 416.
        """
        m = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
        if not m:
            return None, 0
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "" and end_s == "":
            return None, 0
        if start_s == "":
            # suffix: last N bytes
            n = int(end_s)
            if n == 0:
                return None, 0
            start = max(0, size - n)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        if start >= size or start > end:
            return None, 0
        end = min(end, size - 1)
        return start, end

    def _stream(self, path: Path, start: int, length: int) -> None:
        """Stream ``length`` bytes from ``path`` starting at ``start``."""
        remaining = length
        with open(path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    # Client closed the connection (e.g. user seeked away).
                    break
                remaining -= len(chunk)


def run_server(
    host: str,
    port: int,
    repo: Repository,
    storage: FileStorage,
    jobs: RunJobManager | None = None,
) -> None:
    """Block serving the web UI until interrupted (Ctrl+C)."""
    server = WebServer((host, port), WebHandler, repo, storage, jobs=jobs)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("web server stopping")
    finally:
        server.server_close()
