from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from creator_agent.collector.base import CollectFilter
from creator_agent.collector.douyin import collector as collector_module
from creator_agent.collector.douyin.collector import DouyinCollector
from creator_agent.collector.douyin.meta import VideoMeta
from creator_agent.models.creator import Creator

_VID = re.compile(r"/video/(\d+)")

# Window: 2024-06-15 00:00 UTC .. 2024-06-16 00:00 UTC
WINDOW_START = datetime(2024, 6, 15, tzinfo=UTC)
WINDOW_END = datetime(2024, 6, 16, tzinfo=UTC)
IN_WINDOW = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
FUTURE = datetime(2024, 6, 16, 12, 0, tzinfo=UTC)  # >= end -> skip
TOO_OLD = datetime(2024, 6, 14, 12, 0, tzinfo=UTC)  # < start -> out-of-window
STALE = datetime(2024, 5, 1, 12, 0, tzinfo=UTC)  # 45 days before start -> pinned/stale


def _filter(max_videos: int | None = None) -> CollectFilter:
    return CollectFilter(start=WINDOW_START, end=WINDOW_END, max_videos=max_videos)


def _creator() -> Creator:
    return Creator(
        id="douyin_test",
        platform="douyin",
        platform_uid="test",
        nickname="Test",
        homepage_url="https://www.douyin.com/user/test",
        added_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def _link(vid: str) -> dict:
    return {"vid": vid, "href": f"https://www.douyin.com/video/{vid}", "title": f"video {vid}"}


def _meta(vid: str, published_at: datetime | None) -> VideoMeta:
    return VideoMeta(
        cdn_url=f"https://v26-web.douyinvod.com/{vid}.mp4",
        title=f"video {vid}",
        description=f"desc {vid}",
        likes=int(vid),
        published_at=published_at,
        cover_url=f"https://p.douyinpic.com/{vid}.jpg",
    )


class FakePage:
    """Homepage page stand-in. ``evaluate`` returns video links for the
    ``collect_video_links`` JS (querySelectorAll) and grows the visible set for
    the ``_scroll_down`` JS (scrollBy)."""

    def __init__(self, all_links: list[dict], grow_per_scroll: int = 3) -> None:
        self._all = all_links
        self._grow = grow_per_scroll
        self._visible = min(grow_per_scroll, len(all_links))
        self.closed = False
        self.goto_called = False

    def evaluate(self, script: str):
        if "querySelectorAll" in script:
            return list(self._all[: self._visible])
        # scrollBy -> reveal more links
        self._visible = min(self._visible + self._grow, len(self._all))
        return None

    def goto(self, *_a, **_k) -> None:
        self.goto_called = True

    def wait_for_selector(self, *_a, **_k) -> None:
        pass

    def wait_for_timeout(self, *_a, **_k) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    def new_page(self) -> FakePage:
        return self._page


@pytest.fixture
def patched_fetch(monkeypatch):
    """Replace fetch_video_meta in the collector namespace with a lookup by vid.

    Returns the backing dict; tests populate it with ``{vid: VideoMeta}``.
    """
    metas: dict[str, VideoMeta] = {}

    def _fake_fetch(url: str, browser, timeout: int) -> VideoMeta:
        vid = _VID.search(url).group(1)
        return metas[vid]

    monkeypatch.setattr(collector_module, "fetch_video_meta", _fake_fetch)
    return metas


def _run(links: list[dict], max_videos: int | None = None) -> tuple[list, FakePage]:
    collector = DouyinCollector(scroll_delay=(0.0, 0.0))
    page = FakePage(links)
    browser = FakeBrowser(page)
    result = collector.collect(_creator(), _filter(max_videos), browser)
    return result, page


def test_collects_in_window_videos(patched_fetch):
    patched_fetch.update({"1": _meta("1", IN_WINDOW), "2": _meta("2", IN_WINDOW), "3": _meta("3", IN_WINDOW)})
    links = [_link("1"), _link("2"), _link("3")]

    result, page = _run(links)

    assert [cv.platform_vid for cv in result] == ["1", "2", "3"]
    assert page.closed is True


def test_stops_after_out_of_window_threshold(patched_fetch):
    # v1 in window, then two too-old videos -> stop (threshold = 2).
    patched_fetch.update({"1": _meta("1", IN_WINDOW), "2": _meta("2", TOO_OLD), "3": _meta("3", TOO_OLD)})
    links = [_link("1"), _link("2"), _link("3")]

    result, page = _run(links)

    assert [cv.platform_vid for cv in result] == ["1"]
    assert page.closed is True


def test_skips_future_videos_and_keeps_searching(patched_fetch):
    # v1 is newer than the window (>= end), v2 is in window -> v2 collected.
    patched_fetch.update({"1": _meta("1", FUTURE), "2": _meta("2", IN_WINDOW)})
    links = [_link("1"), _link("2")]

    result, _ = _run(links)

    assert [cv.platform_vid for cv in result] == ["2"]


def test_skips_pinned_stale_videos_without_early_stop(patched_fetch):
    # Top of page has pinned videos from months ago (stale), then today's video,
    # then the feed scrolls past the window. Stale videos must NOT trigger the
    # out-of-window stop, else today's video is never reached (the real-world
    # bug: Douyin pins old "selected works" above the chronological feed).
    patched_fetch.update(
        {
            "1": _meta("1", STALE),  # pinned (stale) -> skip, no count
            "2": _meta("2", STALE),  # pinned (stale) -> skip, no count
            "3": _meta("3", IN_WINDOW),  # today -> collect
            "4": _meta("4", TOO_OLD),  # recent-past -> count=1
            "5": _meta("5", TOO_OLD),  # recent-past -> count=2 -> stop
        }
    )
    links = [_link("1"), _link("2"), _link("3"), _link("4"), _link("5")]

    result, _ = _run(links)

    assert [cv.platform_vid for cv in result] == ["3"]


def test_respects_max_videos(patched_fetch):
    patched_fetch.update({"1": _meta("1", IN_WINDOW), "2": _meta("2", IN_WINDOW), "3": _meta("3", IN_WINDOW)})
    links = [_link("1"), _link("2"), _link("3")]

    result, page = _run(links, max_videos=1)

    assert [cv.platform_vid for cv in result] == ["1"]
    assert page.closed is True


def test_published_at_none_treated_as_in_window(patched_fetch):
    # Detail XHR not captured -> published_at None -> collected (not skipped, not
    # counted as out-of-window).
    patched_fetch.update({"1": _meta("1", None)})
    links = [_link("1")]

    result, _ = _run(links)

    assert len(result) == 1
    assert result[0].platform_vid == "1"


def test_collected_video_carries_rich_metadata(patched_fetch):
    patched_fetch.update({"1": _meta("1", IN_WINDOW)})
    links = [_link("1")]

    result, _ = _run(links)

    cv = result[0]
    assert cv.title == "video 1"
    assert cv.description == "desc 1"
    assert str(cv.cover_url) == "https://p.douyinpic.com/1.jpg"
    assert str(cv.video_url) == "https://v26-web.douyinvod.com/1.mp4"
    assert cv.published_at == IN_WINDOW
    assert cv.stats.likes == 1
    assert cv.hashtags == []


def test_list_page_cover_preferred_over_detail_cover(patched_fetch):
    # The card <img> cover (what users see on Douyin) must win over the
    # aweme-detail video.cover field, which resolves to a different image.
    patched_fetch.update({"1": _meta("1", IN_WINDOW)})  # meta.cover_url = p.douyinpic.com/1.jpg
    links = [{"vid": "1", "href": "https://www.douyin.com/video/1", "title": "video 1",
              "cover": "https://p.douyinpic.com/list-cover-1.jpeg"}]

    result, _ = _run(links)

    assert str(result[0].cover_url) == "https://p.douyinpic.com/list-cover-1.jpeg"


def test_falls_back_to_detail_cover_when_list_cover_missing(patched_fetch):
    # Card img not captured (lazy-loaded / off-screen) -> use aweme-detail cover.
    patched_fetch.update({"1": _meta("1", IN_WINDOW)})
    links = [_link("1")]  # no cover key

    result, _ = _run(links)

    assert str(result[0].cover_url) == "https://p.douyinpic.com/1.jpg"


def test_skips_video_when_meta_fetch_raises(patched_fetch, monkeypatch):
    # A failed metadata fetch for one video must not abort the whole collection.
    patched_fetch.update({"1": _meta("1", IN_WINDOW), "2": _meta("2", IN_WINDOW), "3": _meta("3", IN_WINDOW)})
    links = [_link("1"), _link("2"), _link("3")]

    def flaky(url, browser, timeout):
        if "video/2" in url:
            raise RuntimeError("simulated nav failure")
        vid = _VID.search(url).group(1)
        return patched_fetch[vid]

    monkeypatch.setattr(collector_module, "fetch_video_meta", flaky)
    result, _ = _run(links)

    assert [cv.platform_vid for cv in result] == ["1", "3"]
