from __future__ import annotations

from creator_agent.asr.vad_chunker import merge_speech_intervals


def test_empty_and_invalid_intervals():
    assert merge_speech_intervals([]) == []
    assert merge_speech_intervals([(2.0, 1.0)]) == []  # end <= start dropped


def test_short_gaps_merge_long_gaps_split():
    chunks = merge_speech_intervals([(0.0, 1.0), (1.2, 2.0), (5.0, 6.0)], max_gap=0.3, pad=0.0)
    assert chunks == [(0.0, 2.0), (5.0, 6.0)]


def test_unsorted_input_is_sorted():
    chunks = merge_speech_intervals([(5.0, 6.0), (0.0, 1.0)], max_gap=0.3, pad=0.0)
    assert chunks == [(0.0, 1.0), (5.0, 6.0)]


def test_oversized_chunk_splits_at_largest_internal_gap():
    # max_gap=1.5 merges everything into 0..12 (12s > max_chunk=10).
    # Internal gaps: 2.0->3.0 (1s), 10.0->10.5 (0.5s); the largest is at 2..3,
    # so split there -> 0..2 and 3..12; 3..12 is 9s, fits.
    intervals = [(0.0, 2.0), (3.0, 10.0), (10.5, 12.0)]
    chunks = merge_speech_intervals(intervals, max_gap=1.5, max_chunk=10.0, pad=0.0)
    assert chunks == [(0.0, 2.0), (3.0, 12.0)]


def test_oversized_split_recurses():
    # All gaps <= max_gap so everything merges to 0..30 (30s > max_chunk=15).
    # Split at first largest gap -> 0..10 | 10.2..30; the right half (19.8s)
    # still exceeds 15s, so it splits again at its only internal gap.
    intervals = [(0.0, 10.0), (10.2, 20.0), (20.4, 30.0)]
    chunks = merge_speech_intervals(intervals, max_gap=0.3, max_chunk=15.0, pad=0.0)
    assert chunks == [(0.0, 10.0), (10.2, 20.0), (20.4, 30.0)]


def test_single_long_interval_kept_intact():
    # One 60s speech interval with no silence: nowhere to split.
    chunks = merge_speech_intervals([(0.0, 60.0)], max_chunk=30.0, pad=0.0)
    assert chunks == [(0.0, 60.0)]


def test_padding_applied_and_clamped():
    chunks = merge_speech_intervals([(0.05, 1.0), (2.0, 3.0)], max_gap=0.1, pad=0.1, duration=3.05)
    assert chunks[0] == (0.0, 1.1)  # start clamped at 0
    assert chunks[1] == (1.9, 3.05)  # end clamped at duration
