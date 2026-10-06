from __future__ import annotations

from creator_agent.asr.subtitle import parse_srt, segments_to_srt, write_srt
from creator_agent.models.transcript import TranscriptSegment


def _seg(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def test_basic_srt_format():
    srt = segments_to_srt([_seg(1.5, 2.0, "こんにちは"), _seg(65.25, 66.0, "世界")])
    assert srt == ("1\n00:00:01,500 --> 00:00:02,000\nこんにちは\n\n2\n00:01:05,250 --> 00:01:06,000\n世界\n")


def test_hours_and_millis_rounding():
    srt = segments_to_srt([_seg(3661.2345, 3662.0, "テスト")])
    assert "01:01:01,234 --> 01:01:02,000" in srt  # .2345 rounds down (banker's)
    assert "01:01:01," in srt


def test_negative_start_clamped_to_zero():
    srt = segments_to_srt([_seg(-0.5, 1.0, "テスト")])
    assert "00:00:00,000 --> 00:00:01,000" in srt


def test_empty_segments_skipped_and_renumbered():
    srt = segments_to_srt([_seg(0, 1, "  "), _seg(1, 2, "ある"), _seg(2, 3, ""), _seg(3, 4, "いる")])
    assert srt.startswith("1\n00:00:01,000 --> 00:00:02,000\nある")
    assert "2\n00:00:03,000 --> 00:00:04,000\nいる" in srt
    assert "3\n" not in srt


def test_empty_input_returns_empty_string():
    assert segments_to_srt([]) == ""


def test_speaker_not_rendered_in_srt():
    # Speaker labels stay in .txt / .transcript.json; subtitles never show them.
    seg = TranscriptSegment(start=1.0, end=2.0, text="おはよう", speaker="話者A")
    srt = segments_to_srt([seg])
    assert "話者" not in srt
    assert "\nおはよう\n" in srt


def test_filler_cues_are_kept():
    # Full recognition: short interjections (うん。/はい。/あっ …) stay in the
    # subtitles — dropping them silently lost real lines (936#2 regression).
    srt = segments_to_srt([_seg(0, 1, "うん。"), _seg(1, 2, "ある"), _seg(2, 3, "はい。"), _seg(3, 4, "いる")])
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,000\nうん。")
    assert "2\n00:00:01,000 --> 00:00:02,000\nある" in srt
    assert "3\n00:00:02,000 --> 00:00:03,000\nはい。" in srt
    assert "4\n00:00:03,000 --> 00:00:04,000\nいる" in srt


def test_parse_srt_roundtrip(tmp_path):
    segs = [_seg(1.5, 2.0, "こんにちは"), _seg(65.25, 66.0, "世界")]
    path = write_srt(segs, tmp_path / "a.srt")
    parsed = parse_srt(path)
    assert [(s.start, s.end, s.text) for s in parsed] == [(1.5, 2.0, "こんにちは"), (65.25, 66.0, "世界")]


def test_parse_srt_multiline_and_crlf(tmp_path):
    content = (
        "1\r\n00:00:01,000 --> 00:00:02,000\r\n一行目\r\n二行目\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n次\r\n"
    )
    path = tmp_path / "b.srt"
    # newline="" disables Windows text-mode \n->\r\n translation so the literal
    # \r\n in `content` is written verbatim (otherwise the file ends up \r\r\n
    # and reads back as \n\n, breaking the CRLF case on Windows only).
    path.write_text(content, encoding="utf-8", newline="")
    parsed = parse_srt(path)
    assert [s.text for s in parsed] == ["一行目\n二行目", "次"]


def test_write_srt(tmp_path):
    out = write_srt([_seg(0, 1.5, "テスト")], tmp_path / "sub" / "a.srt")
    content = out.read_text(encoding="utf-8")
    assert content.startswith("1\n00:00:00,000 --> 00:00:01,500\nテスト")
    assert content.endswith("\n")
