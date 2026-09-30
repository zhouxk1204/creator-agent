from __future__ import annotations

from creator_agent.asr.speaker_turns import build_speaker_chunks, smooth_labels, speaker_names


def test_smooth_labels_flips_singletons():
    assert smooth_labels([0, 0, 1, 0, 0]) == [0, 0, 0, 0, 0]


def test_smooth_labels_keeps_real_turns():
    assert smooth_labels([0, 0, 1, 1, 0, 0]) == [0, 0, 1, 1, 0, 0]


def test_smooth_labels_short_input_passthrough():
    assert smooth_labels([1]) == [1]
    assert smooth_labels([0, 1]) == [0, 1]


def test_speaker_change_splits_chunk():
    intervals = [(0.0, 1.0), (1.2, 2.0), (2.2, 3.0), (3.2, 4.0)]
    labels = [0, 0, 1, 1]
    chunks = build_speaker_chunks(intervals, labels, max_gap=0.3, pad=0.0)
    assert [(s, e) for s, e, _ in chunks] == [(0.0, 2.0), (2.2, 4.0)]
    assert [lab for _, _, lab in chunks] == [0, 1]


def test_same_speaker_across_turn_merges_again():
    # A-B-A alternation: three chunks even though gaps are small.
    intervals = [(0.0, 1.0), (1.2, 2.0), (2.2, 3.0)]
    labels = [0, 1, 0]
    chunks = build_speaker_chunks(intervals, labels, max_gap=0.3, pad=0.0)
    assert [(s, e, lab) for s, e, lab in chunks] == [(0.0, 1.0, 0), (1.2, 2.0, 1), (2.2, 3.0, 0)]


def test_oversized_run_splits_at_internal_gap():
    # One speaker for 20s > max_chunk=15; internal gap at 10.0->10.5 splits it.
    intervals = [(0.0, 10.0), (10.5, 20.0)]
    labels = [0, 0]
    chunks = build_speaker_chunks(intervals, labels, max_gap=0.6, max_chunk=15.0, pad=0.0)
    assert [(s, e) for s, e, _ in chunks] == [(0.0, 10.0), (10.5, 20.0)]


def test_padding_and_duration_clamp():
    chunks = build_speaker_chunks([(1.0, 2.0)], [0], pad=0.5, duration=2.2)
    assert chunks == [(0.5, 2.2, 0)]


def test_mismatched_lengths_return_empty():
    assert build_speaker_chunks([(0.0, 1.0)], [], pad=0.0) == []
    assert build_speaker_chunks([], [], pad=0.0) == []


def test_speaker_names_in_first_appearance_order():
    assert speaker_names([2, 2, 5, 2, 9]) == {2: "話者A", 5: "話者B", 9: "話者C"}
