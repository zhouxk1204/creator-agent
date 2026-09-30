from __future__ import annotations

from creator_agent.models.transcript import TranscriptSegment
from creator_agent.translate.alignment import align_cues, join_text


def _seg(start: float, end: float, text: str = "x") -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def test_identical_cues_all_same():
    a = [_seg(0, 1), _seg(1, 2), _seg(2, 3)]
    b = [_seg(0, 1), _seg(1, 2), _seg(2, 3)]
    als = align_cues(a, b)
    assert [al.kind for al in als] == ["same", "same", "same"]
    assert all(al.ai == al.final for al in als)


def test_time_shift_within_tol_is_same_beyond_is_time_changed():
    a = [_seg(0, 1), _seg(1, 2)]
    b = [_seg(0.02, 1.03), _seg(1.2, 2.0)]
    als = align_cues(a, b)
    assert als[0].kind == "same"
    assert als[1].kind == "time_changed" and als[1].time_changed


def test_split_one_to_two():
    a = [_seg(0, 2, "ab")]
    b = [_seg(0, 1, "a"), _seg(1, 2, "b")]
    als = align_cues(a, b)
    assert len(als) == 1
    assert als[0].kind == "split"
    assert als[0].ai == [0] and als[0].final == [0, 1]


def test_merge_two_to_one():
    a = [_seg(0, 1, "a"), _seg(1, 2, "b")]
    b = [_seg(0, 2, "ab")]
    als = align_cues(a, b)
    assert len(als) == 1
    assert als[0].kind == "merge"
    assert als[0].ai == [0, 1] and als[0].final == [0]


def test_mixed_sequence():
    a = [_seg(0, 1, "a"), _seg(1, 2, "b"), _seg(2, 4, "cd"), _seg(4, 5, "e")]
    b = [_seg(0, 1, "a"), _seg(1, 2, "B"), _seg(2, 3, "c"), _seg(3, 4, "d"), _seg(4, 5, "e")]
    als = align_cues(a, b)
    assert [al.kind for al in als] == ["same", "same", "split", "same"]


def test_tail_only_on_one_side():
    a = [_seg(0, 1), _seg(1, 2)]
    b = [_seg(0, 1)]
    als = align_cues(a, b)
    assert als[-1].ai == [1] and als[-1].final == []


def test_join_text_normalizes_whitespace():
    segs = [_seg(0, 1, " 多  行\n文本 "), _seg(1, 2, "次")]
    assert join_text(segs, [0, 1]) == "多 行 文本 次"
