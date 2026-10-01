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


def test_global_retime_stays_per_cue_not_megablock():
    # Human nudged every boundary by ~0.1-0.2s (beyond tol): blocks must stay
    # 1:1 (time_changed) instead of snowballing into one giant split block.
    a = [_seg(6.5, 7.6), _seg(8.2, 9.0), _seg(10.0, 11.8)]
    b = [_seg(6.6, 7.4), _seg(8.2, 9.3), _seg(10.0, 12.1)]
    als = align_cues(a, b)
    assert len(als) == 3
    assert all(al.kind == "time_changed" for al in als)
    assert [al.ai for al in als] == [[0], [1], [2]]
    assert [al.final for al in als] == [[0], [1], [2]]


def test_true_split_still_groups_when_closer():
    # 1 AI cue genuinely split into 2 final cues (with slight drift): the
    # extension brings the ends closer, so the block must still group.
    a = [_seg(0, 8, "ab")]
    b = [_seg(0, 4.2, "a"), _seg(4.2, 7.9, "b")]
    als = align_cues(a, b)
    assert len(als) == 1
    assert als[0].kind == "split"
    assert als[0].ai == [0] and als[0].final == [0, 1]


def test_join_text_normalizes_whitespace():
    segs = [_seg(0, 1, " 多  行\n文本 "), _seg(1, 2, "次")]
    assert join_text(segs, [0, 1]) == "多 行 文本 次"
