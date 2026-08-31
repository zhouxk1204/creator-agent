"""Tests for the local web UI server (stdlib http.server).

Boots a WebServer on an ephemeral port against a temp repo + storage with two
videos (one with a transcript, one without) and real fake files on disk, then
asserts routes, Range/206 streaming, 404s, path-traversal rejection, and
transcript rendering via http.client.
"""

from __future__ import annotations

import http.client
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from creator_agent.models.creator import Creator
from creator_agent.models.transcript import Transcript, TranscriptSegment
from creator_agent.models.video import CollectedVideo, VideoStats, VideoStatus
from creator_agent.repository.sqlite_repo import Repository
from creator_agent.storage.file_storage import FileStorage
from creator_agent.web.server import WebHandler, WebServer, _is_safe_id

# -- fixtures -----------------------------------------------------------------


def _creator() -> Creator:
    return Creator(
        id="douyin_test123",
        platform="douyin",
        platform_uid="test123",
        nickname="Test Creator",
        homepage_url="https://www.douyin.com/user/test123",
        added_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def _collected(vid: str, title: str) -> CollectedVideo:
    return CollectedVideo(
        platform="douyin",
        platform_vid=vid,
        title=title,
        description=f"{title} 的描述",
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
        stats=VideoStats(likes=824, comments=57, favorites=35, shares=3),
        hashtags=["测试", "标签"],
    )


@pytest.fixture
def app(tmp_path):
    """Boot a web server on an ephemeral port with two sample videos."""
    repo = Repository(tmp_path / "test.db")
    storage = FileStorage(tmp_path / "storage")
    creator = _creator()
    repo.add_creator(creator)

    # Video 1: downloaded + transcribed (ASR_DONE), with files on disk.
    v1 = repo.upsert_collected(creator, _collected("abc123", "有文案的视频"))
    repo.advance_status(v1.id, VideoStatus.VIDEO_DOWNLOADED)
    repo.advance_status(v1.id, VideoStatus.ASR_DONE)
    storage.save_cover(creator, v1, b"FAKECOVERBYTES")
    storage.save_video_file(creator, v1, b"X" * 10000)
    storage.save_transcript(
        creator,
        v1,
        Transcript(
            video_id=v1.id,
            text="这是完整文案。第一句第二句。",
            segments=[
                TranscriptSegment(start=0.0, end=2.0, text="第一句"),
                TranscriptSegment(start=2.0, end=4.0, text="第二句"),
            ],
            language="zh",
            model="paraformer-zh",
            created_at=datetime(2024, 6, 15, tzinfo=UTC),
        ),
    )

    # Video 2: downloaded but NOT transcribed (no transcript.json).
    v2 = repo.upsert_collected(creator, _collected("def456", "无文案的视频"))
    repo.advance_status(v2.id, VideoStatus.VIDEO_DOWNLOADED)
    storage.save_cover(creator, v2, b"FAKECOVER2")
    storage.save_video_file(creator, v2, b"Y" * 5000)

    server = WebServer(("127.0.0.1", 0), WebHandler, repo, storage)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield SimpleNamespace(
        host="127.0.0.1",
        port=port,
        with_ts=v1.id,
        without_ts=v2.id,
        creator_id=creator.id,
        video_size=10000,
    )

    server.shutdown()
    server.server_close()
    repo.close()


def _get(host: str, port: str | int, path: str, headers: dict | None = None):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def _head(host: str, port: str | int, path: str):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("HEAD", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


# -- list page ----------------------------------------------------------------


def test_list_page_lists_both_videos(app):
    resp, body = _get(app.host, app.port, "/")
    assert resp.status == 200
    assert "text/html" in resp.getheader("Content-Type", "")
    text = body.decode("utf-8")
    assert "有文案的视频" in text
    assert "无文案的视频" in text
    assert "Test Creator" in text  # creator nickname in filter dropdown
    assert "824" in text  # likes count


# -- detail page --------------------------------------------------------------


def test_detail_page_with_transcript_shows_player_and_text(app):
    resp, body = _get(app.host, app.port, f"/video/{app.with_ts}")
    assert resp.status == 200
    text = body.decode("utf-8")
    assert "<video" in text
    assert f"/stream/{app.with_ts}" in text  # player sources the stream endpoint
    assert f"/cover/{app.with_ts}" in text  # poster
    assert "解说文案" in text
    assert "第一句" in text  # transcript segment rendered
    assert "暂无文案" not in text  # not the placeholder
    assert "824" in text  # likes


def test_detail_page_without_transcript_shows_placeholder(app):
    resp, body = _get(app.host, app.port, f"/video/{app.without_ts}")
    assert resp.status == 200
    text = body.decode("utf-8")
    assert "暂无文案" in text
    assert "第一句" not in text


def test_unknown_video_returns_404(app):
    resp, _ = _get(app.host, app.port, "/video/douyin_does_not_exist")
    assert resp.status == 404


# -- media: cover + stream ----------------------------------------------------


def test_cover_served_with_image_type(app):
    resp, body = _get(app.host, app.port, f"/cover/{app.with_ts}")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "image/jpeg"
    assert body == b"FAKECOVERBYTES"


def test_stream_full_file_200_accept_ranges(app):
    resp, body = _get(app.host, app.port, f"/stream/{app.with_ts}")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "video/mp4"
    assert resp.getheader("Accept-Ranges") == "bytes"
    assert resp.getheader("Content-Length") == str(app.video_size)
    assert len(body) == app.video_size


def test_stream_range_returns_206(app):
    resp, body = _get(app.host, app.port, f"/stream/{app.with_ts}", headers={"Range": "bytes=0-1023"})
    assert resp.status == 206
    assert resp.getheader("Content-Range") == f"bytes 0-1023/{app.video_size}"
    assert resp.getheader("Content-Length") == "1024"
    assert len(body) == 1024
    assert body == b"X" * 1024


def test_stream_range_open_ended(app):
    resp, body = _get(app.host, app.port, f"/stream/{app.with_ts}", headers={"Range": "bytes=9000-"})
    assert resp.status == 206
    assert resp.getheader("Content-Range") == f"bytes 9000-9999/{app.video_size}"
    assert len(body) == 1000


def test_stream_range_suffix_last_n(app):
    resp, body = _get(app.host, app.port, f"/stream/{app.with_ts}", headers={"Range": "bytes=-500"})
    assert resp.status == 206
    assert resp.getheader("Content-Range") == f"bytes 9500-9999/{app.video_size}"
    assert len(body) == 500


def test_stream_range_unsatisfiable_returns_416(app):
    resp, _ = _get(app.host, app.port, f"/stream/{app.with_ts}", headers={"Range": "bytes=99999-"})
    assert resp.status == 416
    assert resp.getheader("Content-Range") == f"bytes */{app.video_size}"


def test_head_stream_returns_headers_no_body(app):
    resp, body = _head(app.host, app.port, f"/stream/{app.with_ts}")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "video/mp4"
    assert body == b""


# -- path safety --------------------------------------------------------------


def test_path_traversal_encoded_rejected(app):
    # ..%2F decodes to ../ -> _is_safe_id returns None -> 404.
    resp, _ = _get(app.host, app.port, "/cover/..%2F..%2Fetc%2Fpasswd")
    assert resp.status == 404


def test_unknown_route_returns_404(app):
    resp, _ = _get(app.host, app.port, "/nope/nada")
    assert resp.status == 404


def test_is_safe_id_rejects_traversal():
    assert _is_safe_id("..%2Fetc") is None
    assert _is_safe_id("../etc") is None
    assert _is_safe_id("a\\b") is None
    assert _is_safe_id("douyin_7662286666366766346") == "douyin_7662286666366766346"
    # Creator ids contain dots (base64-ish) and must pass.
    assert _is_safe_id("douyin_MS4wLjAB.AAAA") == "douyin_MS4wLjAB.AAAA"


# -- _parse_range unit tests --------------------------------------------------


def test_parse_range_closed():
    assert WebHandler._parse_range("bytes=0-1023", 10000) == (0, 1023)


def test_parse_range_open_ended():
    assert WebHandler._parse_range("bytes=9000-", 10000) == (9000, 9999)


def test_parse_range_suffix():
    assert WebHandler._parse_range("bytes=-500", 10000) == (9500, 9999)


def test_parse_range_unsatisfiable():
    assert WebHandler._parse_range("bytes=99999-", 10000) == (None, 0)


def test_parse_range_malformed():
    assert WebHandler._parse_range("bytes=abc", 10000) == (None, 0)
    assert WebHandler._parse_range("items=0-10", 10000) == (None, 0)
