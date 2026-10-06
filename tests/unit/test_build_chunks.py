"""Tests for vad_chunker.build_chunks — the targeted 3-8s chunker with a
hard cap and natural-pause force-splitting."""

from __future__ import annotations

from creator_agent.asr.vad_chunker import (
    REASON_MERGED,
    REASON_NORMAL,
    REASON_SPLIT_HARD,
    REASON_SPLIT_NATURAL,
    build_chunks,
)

KW = dict(merge_gap=0.3, target_min=3.0, target_max=8.0, hard_max=15.0, pad=0.0)


def test_empty_and_invalid():
    assert build_chunks([], **KW) == []
    assert build_chunks([(2.0, 1.0)], **KW) == []


def test_close_segments_merge_into_one_chunk():
    # Gaps of 0.3s merge into one utterance group -> one NORMAL chunk.
    ivs = [(0.2, 1.4), (1.7, 3.2), (3.5, 5.8)]
    chunks = build_chunks(ivs, **KW)
    assert len(chunks) == 1
    assert chunks[0]["start"] == 0.2 and chunks[0]["end"] == 5.8
    assert chunks[0]["reason"] == REASON_NORMAL
    assert chunks[0]["segments"] == 3


def test_far_groups_pack_until_target_max():
    # Groups (gap > merge_gap): 0-2, 3-5, 6-9, 20-22.
    # 0-2 + 3-5 + 6-9 span 9s > target_max 8 -> close after the second group:
    # chunk1 = 0..5 (2 groups, MERGED), chunk2 = 6..9, chunk3 = 20..22.
    ivs = [(0.0, 2.0), (3.0, 5.0), (6.0, 9.0), (20.0, 22.0)]
    chunks = build_chunks(ivs, **KW)
    assert [(c["start"], c["end"]) for c in chunks] == [(0.0, 5.0), (6.0, 9.0), (20.0, 22.0)]
    assert chunks[0]["reason"] == REASON_MERGED
    assert chunks[1]["reason"] == REASON_NORMAL


def test_undersized_chunk_may_stretch_past_target_up_to_hard_max():
    # 0-2 then 3-12 (9s group). 0-2 is below target_min, adding keeps the
    # span (12s) under hard_max -> one chunk 0..12 despite exceeding target_max.
    ivs = [(0.0, 2.0), (3.0, 12.0)]
    chunks = build_chunks(ivs, **KW)
    assert len(chunks) == 1
    assert (chunks[0]["start"], chunks[0]["end"]) == (0.0, 12.0)


def test_undersized_chunk_closes_when_stretch_would_exceed_hard_max():
    # 0-1.5 then 3-20. The 17s group is hard-split at target_max into 3-11 and
    # 11-20. The undersized head (1.5s < target_min) may stretch: adding 3-11
    # spans 11s <= hard_max, so it merges in; the tail becomes its own chunk.
    ivs = [(0.0, 1.5), (3.0, 20.0)]
    chunks = build_chunks(ivs, **KW)
    assert [(c["start"], c["end"]) for c in chunks] == [(0.0, 11.0), (11.0, 20.0)]
    assert all(c["end"] - c["start"] <= KW["hard_max"] for c in chunks)
    assert all(c["reason"] == REASON_SPLIT_HARD for c in chunks)


def test_oversized_group_splits_at_natural_pause_near_target():
    # One merged group 0..18 (gaps 0.2s each <= merge_gap) with pauses at
    # ~6 and ~12. target_max=8 -> split at the pause closest to 8 (at 8.2),
    # giving 0..8 and 8.2..18; the right piece (9.8s) fits hard_max.
    ivs = [(0.0, 6.0), (6.2, 8.0), (8.2, 12.0), (12.2, 18.0)]
    chunks = build_chunks(ivs, **KW)
    assert [(c["start"], c["end"]) for c in chunks] == [(0.0, 8.0), (8.2, 18.0)]
    assert all(c["reason"] == REASON_SPLIT_NATURAL for c in chunks)


def test_single_long_interval_is_hard_split():
    # 40s of continuous speech, no pause: hard-cut at target_max repeatedly.
    chunks = build_chunks([(0.0, 40.0)], **KW)
    assert [(c["start"], c["end"]) for c in chunks] == [
        (0.0, 8.0),
        (8.0, 16.0),
        (16.0, 24.0),
        (24.0, 32.0),
        (32.0, 40.0),
    ]
    assert all(c["reason"] == REASON_SPLIT_HARD for c in chunks)


def test_hard_cap_is_never_exceeded():
    # Pathological: 0.25s gaps all the way (everything merges), 60s total.
    ivs = [(i * 4.25, i * 4.25 + 4.0) for i in range(15)]
    chunks = build_chunks(ivs, **KW)
    assert all(c["end"] - c["start"] <= KW["hard_max"] for c in chunks)


def test_padding_and_clamp():
    chunks = build_chunks([(0.05, 1.0)], pad=0.1, duration=0.95)
    assert chunks[0]["start"] == 0.0
    assert chunks[0]["end"] == 0.95
