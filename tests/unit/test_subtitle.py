from __future__ import annotations

from creator_agent.asr.subtitle import segments_to_srt, write_srt
from creator_agent.models.transcript import TranscriptSegment


def _seg(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def test_basic_srt_format():
    srt = segments_to_srt([_seg(1.5, 2.0, "こんにちは"), _seg(65.25, 66.0, "世界")])
    assert srt == ("1\n00:00:01,500 --> 00:00:02,000\nこんにちは\n\n2\n00:01:05,250 --> 00:01:06,000\n世界\n")


def test_hours_and_millis_rounding():
    srt = segments_to_srt([_seg(3661.2345, 3662.0, "x")])
    assert "01:01:01,234 --> 01:01:02,000" in srt  # .2345 rounds down (banker's)
    assert "01:01:01," in srt


def test_negative_start_clamped_to_zero():
    srt = segments_to_srt([_seg(-0.5, 1.0, "x")])
    assert "00:00:00,000 --> 00:00:01,000" in srt


def test_empty_segments_skipped_and_renumbered():
    srt = segments_to_srt([_seg(0, 1, "  "), _seg(1, 2, "ある"), _seg(2, 3, ""), _seg(3, 4, "いる")])
    assert srt.startswith("1\n00:00:01,000 --> 00:00:02,000\nある")
    assert "2\n00:00:03,000 --> 00:00:04,000\nいる" in srt
    assert "3\n" not in srt


def test_empty_input_returns_empty_string():
    assert segments_to_srt([]) == ""


def test_speaker_prefix_added():
    seg = TranscriptSegment(start=1.0, end=2.0, text="おはよう", speaker="話者A")
    srt = segments_to_srt([seg])
    assert "\n話者A: おはよう\n" in srt


def test_no_speaker_no_prefix():
    srt = segments_to_srt([_seg(1.0, 2.0, "おはよう")])
    assert "\nおはよう\n" in srt


def test_write_srt(tmp_path):
    out = write_srt([_seg(0, 1.5, "テスト")], tmp_path / "sub" / "a.srt")
    content = out.read_text(encoding="utf-8")
    assert content.startswith("1\n00:00:00,000 --> 00:00:01,500\nテスト")
    assert content.endswith("\n")
