"""Unit tests for the Xiaohongshu share-link parser and note->meta mapping.

No browser and no network: ``parse_note_target`` is exercised on strings only
(short-link resolution is monkeypatched), and the note extraction helpers run on
a hand-built ``__INITIAL_STATE__``-shaped dict.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from creator_agent.collector.xhs import url as url_module
from creator_agent.collector.xhs.meta import (
    note_from_html,
    note_to_meta,
    pick_note,
    resolve_video_url,
    state_blob_from_html,
)
from creator_agent.collector.xhs.url import XhsUrlError, is_xhs_url, parse_note_target

NOTE_ID = "6aa76492000000000d024a7b"
XSEC_TOKEN = "CBm0zyj0BO4sg0CK8mZ7XVMfcjgRLPbD5Ep-oozwEtDMk="

# The real share link from the Xiaohongshu app: canonical note URL + a pile of
# app-only tracking params plus the xsec_token the note page actually needs.
SHARE_URL = (
    f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
    "?app_platform=ios&app_version=9.48&share_from_user_hidden=true&xsec_source=app_share"
    f"&type=video&xsec_token={XSEC_TOKEN}&author_share=1&xhsshare=WeixinSession"
    "&shareRedId=N0pFNzlINEA2NzUyOTgwNjY0OTc9OUw_&apptime=1790386303"
    "&share_id=c27bfacc9b824c3088b928e8688cd32d&track_code=8Ih7gPa2rPz"
)


class TestIsXhsUrl:
    def test_canonical_url(self):
        assert is_xhs_url(SHARE_URL)

    def test_short_link(self):
        assert is_xhs_url("http://xhslink.com/a/abcdEF")

    def test_share_text_with_prose(self):
        assert is_xhs_url(f"看看【超好吃的一碗面】 😆 {SHARE_URL} 复制本条信息，打开【小红书】")

    def test_douyin_url_is_not_xhs(self):
        assert not is_xhs_url("https://www.douyin.com/video/7664966551623322914")

    def test_bare_id_is_not_a_url(self):
        assert not is_xhs_url(NOTE_ID)

    def test_lookalike_domain_is_not_xhs(self):
        assert not is_xhs_url(f"https://notxiaohongshu.com/explore/{NOTE_ID}")

    def test_empty(self):
        assert not is_xhs_url("")


class TestParseNoteTarget:
    def test_full_share_url_keeps_token_and_drops_tracking(self):
        note_id, page_url = parse_note_target(SHARE_URL)
        assert note_id == NOTE_ID
        assert page_url.startswith(f"https://www.xiaohongshu.com/explore/{NOTE_ID}?")
        assert "xsec_token=" in page_url
        assert "xsec_source=app_share" in page_url
        # App-tracking params are dropped so the URL is stable/dedupable.
        for junk in ("apptime", "track_code", "shareRedId", "wechatWid", "app_platform"):
            assert junk not in page_url

    def test_token_is_carried_over_verbatim(self):
        # xsec_token is validated as an opaque string; re-encoding it breaks it.
        _, page_url = parse_note_target(SHARE_URL)
        assert f"xsec_token={XSEC_TOKEN}" in page_url

    def test_share_text_wrapping_the_link(self):
        raw = f"59 看看【面】 😆 {SHARE_URL} 复制本条信息，打开【小红书】App查看精彩内容！"
        note_id, _ = parse_note_target(raw)
        assert note_id == NOTE_ID

    def test_bare_note_id(self):
        note_id, page_url = parse_note_target(NOTE_ID)
        assert note_id == NOTE_ID
        assert page_url == f"https://www.xiaohongshu.com/explore/{NOTE_ID}"

    def test_bare_note_id_is_normalized_to_lowercase(self):
        note_id, _ = parse_note_target(NOTE_ID.upper())
        assert note_id == NOTE_ID

    def test_discovery_item_url(self):
        note_id, page_url = parse_note_target(f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}?xsec_token=abc")
        assert note_id == NOTE_ID
        assert page_url.startswith(f"https://www.xiaohongshu.com/explore/{NOTE_ID}?")
        assert "xsec_token=abc" in page_url

    def test_url_without_any_query_still_parses(self):
        note_id, page_url = parse_note_target(f"https://www.xiaohongshu.com/explore/{NOTE_ID}")
        assert note_id == NOTE_ID
        assert page_url == f"https://www.xiaohongshu.com/explore/{NOTE_ID}"

    def test_short_link_is_resolved(self, monkeypatch):
        resolved = f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={XSEC_TOKEN}&xsec_source=app_share"
        monkeypatch.setattr(url_module, "_resolve_short_link", lambda url, timeout: resolved)
        note_id, page_url = parse_note_target("http://xhslink.com/a/abcdef")
        assert note_id == NOTE_ID
        assert "xsec_token=" in page_url

    def test_empty_input(self):
        with pytest.raises(XhsUrlError):
            parse_note_target("   ")

    def test_no_note_id(self):
        with pytest.raises(XhsUrlError):
            parse_note_target("https://www.xiaohongshu.com/user/profile/5f1b2c3d4e5f6a7b8c9d0e1f")


def _note(**overrides) -> dict:
    """A trimmed-down note object shaped like ``__INITIAL_STATE__``'s.

    Field shapes mirror the live 2026-09 page: string counters, cover under
    ``infoList``, one stream entry per quality tier with a ``size`` field.
    """
    note = {
        "noteId": NOTE_ID,
        "type": "video",
        "title": "",
        "desc": "今天做了番茄牛腩面，汤底超浓！#家常菜 #面食",
        "time": 1790386303000,
        "user": {
            "userId": "5f1b2c3d4e5f6a7b8c9d0e1f",
            "nickname": "小饭",
            "avatar": "https://sns-avatar-qc.xhscdn.com/avatar/abc",
        },
        "interactInfo": {"likedCount": "1.2万", "collectedCount": 345, "commentCount": "67", "shareCount": 8},
        "tagList": [{"name": "家常菜", "type": "topic"}, {"name": "面食", "type": "topic"}],
        "imageList": [
            {
                "fileId": "abc123",
                "infoList": [
                    {"imageScene": "WB_PRV", "url": "http://sns-webpic-qc.xhscdn.com/poster-prv.webp"},
                    {"imageScene": "WB_DFT", "url": "http://sns-webpic-qc.xhscdn.com/poster-dft.webp"},
                ],
            }
        ],
        "video": {
            "capa": {"duration": 63},
            "media": {
                "videoId": "abc",
                "stream": {
                    "h264": [
                        {
                            "masterUrl": "http://sns-video-bd.xhscdn.com/stream/xyz-sd.mp4",
                            "duration": 63000,
                            "size": 10_000,
                        },
                        {
                            "masterUrl": "http://sns-video-bd.xhscdn.com/stream/xyz-hd.mp4",
                            "duration": 63000,
                            "size": 30_000,
                        },
                    ],
                    "h265": [{"masterUrl": "http://sns-video-bd.xhscdn.com/stream/xyz-h265.mp4"}],
                },
            },
        },
    }
    note.update(overrides)
    return note


class TestPickNote:
    def test_picks_first_usable_note(self):
        state = {"note": {"noteDetailMap": {"aaa": {"note": _note()}}}}
        assert pick_note(state)["noteId"] == NOTE_ID

    def test_empty_map(self):
        assert pick_note({"note": {"noteDetailMap": {}}}) is None

    def test_missing_state(self):
        assert pick_note(None) is None
        assert pick_note("nonsense") is None

    def test_entry_without_note_is_skipped(self):
        state = {"note": {"noteDetailMap": {"aaa": {"comments": {}}, "bbb": {"note": _note()}}}}
        assert pick_note(state)["noteId"] == NOTE_ID

    def test_prefers_the_requested_note_id(self):
        # noteDetailMap accumulates every note this browser profile has opened.
        other_id = "aaaaaaaaaaaaaaaaaaaaaaaa"
        state = {
            "note": {
                "noteDetailMap": {
                    other_id: {"note": _note(noteId=other_id, desc="别的笔记")},
                    NOTE_ID: {"note": _note()},
                }
            }
        }
        assert pick_note(state, NOTE_ID)["noteId"] == NOTE_ID

    def test_falls_back_when_requested_id_is_absent(self):
        state = {"note": {"noteDetailMap": {NOTE_ID: {"note": _note()}}}}
        assert pick_note(state, "ffffffffffffffffffffffff")["noteId"] == NOTE_ID


class TestResolveVideoUrl:
    def test_prefers_the_largest_h264_entry(self):
        # One entry per quality tier; size tracks bitrate, so biggest = best.
        assert resolve_video_url(_note()) == "https://sns-video-bd.xhscdn.com/stream/xyz-hd.mp4"

    def test_falls_back_to_first_entry_when_no_sizes(self):
        note = _note()
        note["video"]["media"]["stream"]["h264"] = [{"masterUrl": "http://sns-video-bd.xhscdn.com/stream/xyz.mp4"}]
        assert resolve_video_url(note) == "https://sns-video-bd.xhscdn.com/stream/xyz.mp4"

    def test_falls_back_to_h265_when_no_h264(self):
        note = _note()
        del note["video"]["media"]["stream"]["h264"]
        assert resolve_video_url(note) == "https://sns-video-bd.xhscdn.com/stream/xyz-h265.mp4"

    def test_falls_back_to_origin_key(self):
        note = _note()
        note["video"] = {"consumer": {"originVideoKey": "prepost/abc/def"}}
        assert resolve_video_url(note) == "https://sns-video-bd.xhscdn.com/prepost/abc/def"

    def test_no_video(self):
        assert resolve_video_url({"imageList": []}) is None


class TestNoteToMeta:
    def test_full_mapping(self):
        meta = note_to_meta(_note())
        assert meta.note_id == NOTE_ID
        assert meta.note_type == "video"
        assert meta.cdn_url == "https://sns-video-bd.xhscdn.com/stream/xyz-hd.mp4"
        assert meta.author_uid == "5f1b2c3d4e5f6a7b8c9d0e1f"
        assert meta.author_nickname == "小饭"
        assert meta.likes == 12000
        assert meta.favorites == 345
        assert meta.comments == 67
        assert meta.shares == 8
        assert meta.duration_sec == 63
        assert meta.tags == ["家常菜", "面食"]
        # WB_PRV (the preview the user sees) wins over WB_DFT.
        assert meta.cover_url == "https://sns-webpic-qc.xhscdn.com/poster-prv.webp"
        # note.time is epoch *milliseconds*: 1790386303000 -> 2026-09-26 01:31:43Z
        assert meta.published_at == datetime(2026, 9, 26, 1, 31, 43, tzinfo=UTC)

    def test_cover_prefers_flat_keys_over_infolist(self):
        note = _note()
        note["imageList"][0]["urlDefault"] = "http://sns-webpic-qc.xhscdn.com/flat.jpg"
        assert note_to_meta(note).cover_url == "https://sns-webpic-qc.xhscdn.com/flat.jpg"

    def test_cover_falls_back_to_any_info_url(self):
        note = _note()
        note["imageList"][0]["infoList"] = [{"imageScene": "OTHER", "url": "http://sns-webpic-qc.xhscdn.com/other.jpg"}]
        assert note_to_meta(note).cover_url == "https://sns-webpic-qc.xhscdn.com/other.jpg"

    def test_cover_falls_back_to_video_first_frame(self):
        note = _note(imageList=[], video={"image": {"firstFrame": "http://sns-webpic-qc.xhscdn.com/frame.webp"}})
        assert note_to_meta(note).cover_url == "https://sns-webpic-qc.xhscdn.com/frame.webp"

    def test_desc_hashtag_markers_are_stripped(self):
        meta = note_to_meta(_note(desc="#健康科普[话题]# 正文 #健仔小课堂[话题]#"))
        assert meta.description == "#健康科普# 正文 #健仔小课堂#"

    def test_empty_title_falls_back_to_desc(self):
        meta = note_to_meta(_note())
        assert meta.title == "今天做了番茄牛腩面，汤底超浓！#家常菜 #面食"
        assert meta.description == meta.title

    def test_explicit_title_is_kept(self):
        meta = note_to_meta(_note(title="番茄牛腩面", desc="步骤见图"))
        assert meta.title == "番茄牛腩面"
        assert meta.description == "步骤见图"

    def test_intercepted_url_used_when_stream_missing(self):
        note = _note()
        del note["video"]["media"]
        meta = note_to_meta(note, cdn_url="https://sns-video-hw.xhscdn.com/stream/captured.mp4")
        assert meta.cdn_url == "https://sns-video-hw.xhscdn.com/stream/captured.mp4"

    def test_none_note_is_all_defaults(self):
        meta = note_to_meta(None)
        assert meta.cdn_url is None
        assert meta.likes == 0
        assert meta.published_at is None

    def test_duration_falls_back_to_stream_milliseconds(self):
        note = _note()
        del note["video"]["capa"]
        assert note_to_meta(note).duration_sec == 63

    def test_string_counters_without_suffix(self):
        note = _note(interactInfo={"likedCount": "1,234", "commentCount": "", "collectedCount": None})
        meta = note_to_meta(note)
        assert meta.likes == 1234
        assert meta.comments == 0
        assert meta.favorites == 0

    def test_malformed_fields_do_not_raise(self):
        """Fields come from page-owned JSON - a null/list where an object is
        expected must degrade to defaults, not crash the run."""
        note = _note(
            user=None,
            interactInfo=[],
            tagList=[{"name": "ok"}, "not-a-dict", {}],
            imageList="oops",
            video={"capa": None, "media": {"stream": {"h264": [{"masterUrl": None}, "junk"]}}},
        )
        meta = note_to_meta(note, cdn_url="https://sns-video-bd.xhscdn.com/fallback.mp4")
        assert meta.author_uid == ""
        assert meta.author_nickname == ""
        assert meta.likes == 0
        assert meta.tags == ["ok"]
        assert meta.duration_sec is None
        assert meta.cdn_url == "https://sns-video-bd.xhscdn.com/fallback.mp4"


def _wrap_state(state: dict) -> str:
    """A note document the way the real page ships it: SSR HTML with the state
    blob assigned inside a <script> tag, surrounded by page chrome."""
    blob = json.dumps(state, ensure_ascii=False)
    return (
        "<!DOCTYPE html><html><head><title>x</title></head><body>"
        '<div id="app">skeleton</div>'
        f"<script>window.__INITIAL_STATE__ = {blob}</script>"
        "<script>bootstrap()</script></body></html>"
    )


class TestStateBlobFromHtml:
    def test_extracts_balanced_blob(self):
        html = '<script>window.__INITIAL_STATE__ = {"a":{"b":1}}</script>'
        assert state_blob_from_html(html) == '{"a":{"b":1}}'

    def test_braces_inside_strings_do_not_close_the_blob(self):
        html = 'preamble window.__INITIAL_STATE__ = {"t":"a { } \\"b\\" } tail"} tail'
        assert state_blob_from_html(html) == '{"t":"a { } \\"b\\" } tail"}'

    def test_marker_without_object(self):
        assert state_blob_from_html("<html><body>nothing</body></html>") is None

    def test_truncated_assignment(self):
        assert state_blob_from_html('<script>window.__INITIAL_STATE__ = {"note":') is None


class TestNoteFromHtml:
    def test_round_trip_through_document(self):
        state = {"note": {"noteDetailMap": {NOTE_ID: {"note": _note()}}}}
        note = note_from_html(_wrap_state(state), NOTE_ID)
        assert note["noteId"] == NOTE_ID
        assert note["user"]["nickname"] == "小饭"

    def test_undefined_literals_become_null(self):
        # The real page emits e.g. redId:undefined - not valid JSON.
        blob = (
            '{"note":{"noteDetailMap":{"'
            + NOTE_ID
            + '":{"note":'
            + json.dumps(_note(), ensure_ascii=False)
            + '}}},"user":{"redId":undefined}}'
        )
        html = f"<html><body><script>window.__INITIAL_STATE__ = {blob}</script></body></html>"
        assert note_from_html(html, NOTE_ID)["noteId"] == NOTE_ID

    def test_returns_none_without_marker(self):
        assert note_from_html("<html><body>plain</body></html>") is None

    def test_returns_none_on_truncated_blob(self):
        assert note_from_html('<script>window.__INITIAL_STATE__ = {"note":') is None
