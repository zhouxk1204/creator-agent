from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from creator_agent.downloader import downloader as downloader_module
from creator_agent.downloader.downloader import Downloader, VideoMeta
from creator_agent.models.creator import Creator
from creator_agent.models.video import Video, VideoStatus
from creator_agent.storage.file_storage import FileStorage

CDN_URL = "https://v26-web.douyinvod.com/sign/video/tos/cn/abc.mp4?a=1"
PAGE_URL = "https://www.douyin.com/video/999"
DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=999"

DETAIL_PAYLOAD = {
    "aweme_detail": {
        "aweme_id": "999",
        "desc": "A股收盘点评 #股票 #基金",
        "statistics": {
            "digg_count": 100653,
            "comment_count": 2233,
            "share_count": 4232,
            "collect_count": 30692,
            "play_count": 0,
        },
        "text_extra": [{"hashtag_name": "股票"}, {"hashtag_name": "基金"}, {"hashtag_name": ""}],
        "duration": 190822,
        "create_time": 1749544200,
        "video": {"cover": {"url_list": ["https://p.douyinpic.com/cover.jpg"]}},
    }
}


# --- Fakes -----------------------------------------------------------------


class FakeResponse:
    """Shared by the httpx path (``.content``/``raise_for_status``) and the
    Playwright response handler (``.headers``/``.url``/``.body``)."""

    def __init__(self, content: bytes = b"", status: int = 200, ctype: str = "text/html", url: str = "") -> None:
        self.content = content
        self.status_code = status
        self.headers = {"content-type": ctype}
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def body(self) -> bytes:
        return self.content


class FakeClient:
    """Minimal ``httpx.Client`` stand-in. Behavior via class-level ``behavior``:

    - ``content``: bytes returned on a successful GET (default ``b"<html>"``).
    - ``fail_first_n``: number of leading GET attempts that raise (default 0).
    """

    behavior: dict = {}
    _total_gets: int = 0
    instances: list[FakeClient] = []

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[str] = []
        type(self).instances.append(self)

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def get(self, url: str) -> FakeResponse:
        self.calls.append(url)
        type(self)._total_gets += 1
        if type(self)._total_gets <= FakeClient.behavior.get("fail_first_n", 0):
            raise RuntimeError("simulated httpx failure")
        return FakeResponse(FakeClient.behavior.get("content", b"<html>"))


class FakePage:
    """Playwright ``Page`` stand-in. On ``goto`` it fires the registered
    ``response`` handler once per response in ``responses``.

    ``capture_url`` is a convenience to fire a single video/mp4 response carrying
    that CDN URL (used by the resolve-only tests)."""

    def __init__(self, responses: list[FakeResponse] | None = None, capture_url: str | None = None) -> None:
        self._responses = list(responses) if responses else []
        if capture_url is not None:
            self._responses.append(FakeResponse(b"", ctype="video/mp4", url=capture_url))
        self._handlers: dict[str, object] = {}
        self.goto_calls: list[str] = []
        self.evaluated: list[str] = []
        self.closed = False

    def on(self, event: str, handler) -> None:
        self._handlers[event] = handler

    def goto(self, url: str, **kwargs) -> None:
        self.goto_calls.append(url)
        for resp in self._responses:
            self._handlers["response"](resp)

    def wait_for_timeout(self, _ms: int) -> None:
        pass

    def evaluate(self, script: str) -> None:
        self.evaluated.append(script)

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.new_page_calls = 0
        self.cookies = [{"name": "sid", "value": "abc"}]

    def get_cookies(self, domain: str) -> list[dict]:
        return self.cookies

    def new_page(self) -> FakePage:
        self.new_page_calls += 1
        return self._page


# --- Fixtures --------------------------------------------------------------


@pytest.fixture
def storage(tmp_path):
    return FileStorage(tmp_path / "storage")


@pytest.fixture(autouse=True)
def _reset_fake_httpx(monkeypatch):
    FakeClient.behavior = {}
    FakeClient._total_gets = 0
    FakeClient.instances = []
    monkeypatch.setattr(downloader_module.httpx, "Client", FakeClient)
    monkeypatch.setattr(downloader_module.time, "sleep", lambda *_a, **_kw: None)
    yield


@pytest.fixture
def creator():
    return Creator(
        id="douyin_test123",
        platform="douyin",
        platform_uid="test123",
        nickname="Test Creator",
        homepage_url="https://www.douyin.com/user/test123",
        added_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def _make_video(video_url: str | None = PAGE_URL, cover_url: str | None = None) -> Video:
    return Video(
        id="douyin_v999",
        creator_id="douyin_test123",
        platform="douyin",
        platform_vid="v999",
        title="Test Video",
        video_url=video_url,
        cover_url=cover_url,
        published_at=datetime(2024, 6, 15, tzinfo=UTC),
        collected_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        status=VideoStatus.NEW,
    )


def _new_downloader(storage, page: FakePage | None = None, retries: int = 3) -> tuple[Downloader, FakeBrowser]:
    browser = FakeBrowser(page or FakePage())
    return Downloader(storage=storage, browser=browser, timeout_sec=10, retries=retries), browser


def _detail_response() -> FakeResponse:
    return FakeResponse(json.dumps(DETAIL_PAYLOAD).encode(), ctype="application/json", url=DETAIL_URL)


# --- fetch_video_meta ------------------------------------------------------


def test_fetch_video_meta_returns_title_tags_stats(storage):
    page = FakePage(responses=[FakeResponse(b"", ctype="video/mp4", url=CDN_URL), _detail_response()])
    dl, _ = _new_downloader(storage, page=page)

    meta = dl.fetch_video_meta(PAGE_URL)

    assert meta.cdn_url == CDN_URL
    assert meta.title == "A股收盘点评 #股票 #基金"
    assert meta.description == "A股收盘点评 #股票 #基金"
    assert meta.tags == ["股票", "基金"]  # empty hashtag_name filtered out
    assert meta.likes == 100653
    assert meta.comments == 2233
    assert meta.shares == 4232
    assert meta.favorites == 30692
    assert meta.views is None  # play_count == 0 -> None
    assert meta.duration_sec == 190
    assert meta.published_at == datetime.fromtimestamp(1749544200, tz=UTC)
    assert meta.cover_url == "https://p.douyinpic.com/cover.jpg"
    assert page.closed is True


def test_fetch_video_meta_without_detail_has_empty_fields(storage):
    # Only the CDN URL is captured; no detail XHR -> empty metadata, but cdn_url set.
    page = FakePage(responses=[FakeResponse(b"", ctype="video/mp4", url=CDN_URL)])
    dl, _ = _new_downloader(storage, page=page)

    meta = dl.fetch_video_meta(PAGE_URL)

    assert meta.cdn_url == CDN_URL
    assert meta.title == ""
    assert meta.tags == []
    assert meta.likes == 0
    assert meta.duration_sec is None
    assert meta.published_at is None


def test_fetch_video_meta_with_nothing_captured(storage):
    page = FakePage(responses=[])
    dl, _ = _new_downloader(storage, page=page)

    meta = dl.fetch_video_meta(PAGE_URL)

    assert meta.cdn_url is None
    assert meta.title == ""
    assert isinstance(meta, VideoMeta)


# --- download_video: page URL -> resolve -> httpx -------------------------


def test_page_url_resolves_cdn_then_downloads(storage, creator):
    FakeClient.behavior = {"content": b"MP4BYTES"}
    page = FakePage(capture_url=CDN_URL)
    dl, browser = _new_downloader(storage, page=page)

    path = dl.download_video(creator, _make_video(video_url=PAGE_URL))

    assert path.read_bytes() == b"MP4BYTES"
    assert browser.new_page_calls == 1
    assert page.goto_calls == [PAGE_URL]
    assert page.closed is True
    assert FakeClient.instances[0].calls == [CDN_URL]


def test_direct_url_skips_browser_resolve(storage, creator):
    FakeClient.behavior = {"content": b"MP4BYTES"}
    dl, browser = _new_downloader(storage)

    path = dl.download_video(creator, _make_video(video_url=CDN_URL))

    assert path.read_bytes() == b"MP4BYTES"
    assert browser.new_page_calls == 0
    assert FakeClient.instances[0].calls == [CDN_URL]


def test_direct_url_param_skips_resolve_and_ignores_page_video_url(storage, creator):
    # Caller already resolved the CDN URL via fetch_video_meta; pass it directly
    # so download does not re-navigate, even though video.video_url is a page URL.
    FakeClient.behavior = {"content": b"MP4BYTES"}
    dl, browser = _new_downloader(storage)

    path = dl.download_video(creator, _make_video(video_url=PAGE_URL), direct_url=CDN_URL)

    assert path.read_bytes() == b"MP4BYTES"
    assert browser.new_page_calls == 0
    assert FakeClient.instances[0].calls == [CDN_URL]


def test_direct_url_param_works_without_video_url(storage, creator):
    FakeClient.behavior = {"content": b"MP4BYTES"}
    dl, _ = _new_downloader(storage)

    path = dl.download_video(creator, _make_video(video_url=None), direct_url=CDN_URL)

    assert path.read_bytes() == b"MP4BYTES"


def test_resolve_finds_no_media_url_raises(storage, creator):
    FakeClient.behavior = {"content": b"MP4BYTES"}
    page = FakePage(capture_url=None)
    dl, _ = _new_downloader(storage, page=page)

    with pytest.raises(RuntimeError, match="Could not resolve a direct video URL"):
        dl.download_video(creator, _make_video(video_url=PAGE_URL))
    assert page.closed is True


def test_httpx_retries_then_succeeds(storage, creator):
    FakeClient.behavior = {"fail_first_n": 2, "content": b"OK"}
    dl, browser = _new_downloader(storage)

    path = dl.download_video(creator, _make_video(video_url=CDN_URL))

    assert path.read_bytes() == b"OK"
    assert browser.new_page_calls == 0
    assert len(FakeClient.instances) == 3


def test_httpx_all_attempts_fail_raises(storage, creator):
    FakeClient.behavior = {"fail_first_n": 99}
    dl, _ = _new_downloader(storage, retries=3)

    with pytest.raises(RuntimeError, match="Video download failed after 3 attempts"):
        dl.download_video(creator, _make_video(video_url=CDN_URL))


def test_no_video_url_and_no_direct_url_raises_value_error(storage, creator):
    dl, _ = _new_downloader(storage)
    with pytest.raises(ValueError, match="no video_url"):
        dl.download_video(creator, _make_video(video_url=None))


def test_effect_cdn_urls_are_ignored_during_resolve(storage, creator):
    effect_url = "https://lf3-effectcdn-tos.byteeffecttos.com/obj/ies.fe.effect/abc.mp4"
    page = FakePage(capture_url=effect_url)
    dl, _ = _new_downloader(storage, page=page)

    with pytest.raises(RuntimeError, match="Could not resolve a direct video URL"):
        dl.download_video(creator, _make_video(video_url=PAGE_URL))


# --- download_cover --------------------------------------------------------


def test_download_cover_success(storage, creator):
    FakeClient.behavior = {"content": b"JPEGDATA"}
    dl, _ = _new_downloader(storage)

    path = dl.download_cover(creator, _make_video(cover_url="https://p.douyinpic.com/x.jpg"))

    assert path is not None
    assert path.read_bytes() == b"JPEGDATA"


def test_download_cover_no_url_returns_none(storage, creator):
    dl, _ = _new_downloader(storage)
    assert dl.download_cover(creator, _make_video(cover_url=None)) is None


def test_download_cover_failure_returns_none(storage, creator):
    FakeClient.behavior = {"fail_first_n": 99}
    dl, _ = _new_downloader(storage)
    assert dl.download_cover(creator, _make_video(cover_url="https://p.douyinpic.com/x.jpg")) is None


# --- save_metadata ---------------------------------------------------------


def test_save_metadata_writes_json(storage, creator):
    dl, _ = _new_downloader(storage)
    video = _make_video()

    path = dl.save_metadata(creator, video)

    assert path.exists()
    assert path.name == "metadata.json"
    assert storage.load_metadata(creator, video) is not None
