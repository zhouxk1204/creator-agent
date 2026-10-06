"""Tests for the post-alignment quality gate (align_check)."""

from __future__ import annotations

from creator_agent.asr.align_check import (
    ABNORMAL_DURATION,
    INVALID_TIMING,
    LOW_COVERAGE,
    NO_WORDS,
    OUT_OF_WINDOW,
    check_alignment,
)

TEXT = "ドラえもん、どこにいるの？"
# Words covering the whole text (punctuation dropped by the aligner).
WORDS = [
    ("ドラえもん", 1.31, 2.10),
    ("どこに", 2.20, 2.80),
    ("いるの", 2.85, 3.42),
]
KW = dict(chunk_start=1.0, chunk_end=4.0, pad=0.3)


def test_no_words_fails():
    r = check_alignment(None, TEXT, **KW)
    assert not r.ok and r.reason == NO_WORDS
    r = check_alignment([], TEXT, **KW)
    assert not r.ok and r.reason == NO_WORDS


def test_good_alignment_passes_with_full_coverage():
    r = check_alignment(WORDS, TEXT, **KW)
    assert r.ok
    # 11 of 13 chars covered (、？ are not aligner words).
    assert r.coverage == 11 / 13


def test_low_coverage_fails_below_threshold():
    r = check_alignment([("ドラえもん", 1.31, 2.10)], TEXT, **KW)
    assert not r.ok and r.reason == LOW_COVERAGE
    assert r.coverage == 5 / 13


def test_custom_coverage_threshold():
    r = check_alignment([("ドラえもん", 1.31, 2.10)], TEXT, min_coverage=0.3, **KW)
    assert r.ok


def test_words_may_spill_within_pad_but_not_beyond():
    # Within pad: chunk 1.0-4.0, pad 0.3 -> allowed 0.7..4.3.
    words = [("ドラえもん", 0.75, 1.5), ("どこに", 1.5, 2.5), ("いるの", 2.5, 4.25)]
    assert check_alignment(words, TEXT, **KW).ok
    # Locked onto the PREVIOUS utterance: outside the padded window.
    bad = [("ドラえもん", 0.2, 0.6), ("どこに", 1.5, 2.5), ("いるの", 2.5, 3.42)]
    r = check_alignment(bad, TEXT, **KW)
    assert not r.ok and r.reason == OUT_OF_WINDOW and "ドラえもん" in r.detail


def test_invalid_timing_start_after_end():
    bad = [("ドラえもん", 2.10, 1.31), ("どこに", 2.20, 2.80), ("いるの", 2.85, 3.42)]
    r = check_alignment(bad, TEXT, **KW)
    assert not r.ok and r.reason == INVALID_TIMING


def test_invalid_timing_non_monotonic():
    bad = [("ドラえもん", 1.31, 2.10), ("どこに", 1.90, 2.80), ("いるの", 2.85, 3.42)]
    r = check_alignment(bad, TEXT, **KW)
    assert not r.ok and r.reason == INVALID_TIMING


def test_abnormal_duration():
    too_short = [("ドラえもん", 1.31, 1.315), ("どこに", 2.20, 2.80), ("いるの", 2.85, 3.42)]
    r = check_alignment(too_short, TEXT, **KW)
    assert not r.ok and r.reason == ABNORMAL_DURATION
    too_long = [("ドラえもん", 1.31, 2.10), ("どこに", 2.20, 6.0), ("いるの", 6.0, 6.5)]
    r = check_alignment(too_long, TEXT, chunk_start=1.0, chunk_end=6.5, pad=0.3)
    assert not r.ok and r.reason == ABNORMAL_DURATION


def test_first_failure_wins():
    # Low coverage AND out of window: coverage is checked first.
    r = check_alignment([("ドラえもん", 0.1, 0.5)], TEXT, **KW)
    assert r.reason == LOW_COVERAGE
