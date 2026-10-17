"""Tests for the post-alignment quality gate (align_check)."""

from __future__ import annotations

from creator_agent.asr.align_check import (
    ABNORMAL_DURATION,
    INVALID_TIMING,
    LOW_COVERAGE,
    NO_WORDS,
    OUT_OF_WINDOW,
    check_alignment,
    repair_word_timing,
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


# --- repair_word_timing ----------------------------------------------------


def test_repair_clean_words_pass_through_untouched():
    res = repair_word_timing(WORDS)
    assert res is not None
    repaired, n = res
    assert repaired == [(t, s, e) for t, s, e in WORDS]
    assert n == 0


def test_repair_empty_returns_none():
    assert repair_word_timing(None) is None
    assert repair_word_timing([]) is None


def test_repair_zero_duration_word_expanded():
    # A zero-duration token sandwiched between two well-placed words (the
    # exact Qwen3-ForcedAligner failure behind the drifted cue times).
    words = [("ドラえもん", 1.31, 2.10), ("どこに", 2.10, 2.10), ("いるの", 2.85, 3.42)]
    repaired, n = repair_word_timing(words)
    assert n == 1
    assert repaired[1] == ("どこに", 2.10, 2.15)  # expanded to min_word_sec (0.05)
    # Result is now valid for check_alignment (monotonic, positive durations).
    assert check_alignment(repaired, TEXT, **KW).ok


def test_repair_small_overlap_clamped():
    words = [("ドラえもん", 1.31, 2.10), ("どこに", 2.05, 2.80), ("いるの", 2.85, 3.42)]
    repaired, n = repair_word_timing(words)
    assert n == 1
    assert repaired[1][1] == 2.10  # clamped forward to the previous end
    assert check_alignment(repaired, TEXT, **KW).ok


def test_repair_large_overlap_rejected():
    # Word locked onto the PREVIOUS utterance (overlap > max_overlap_sec):
    # unrepairable, must not be trusted.
    words = [("ドラえもん", 1.31, 2.10), ("どこに", 1.50, 2.80), ("いるの", 2.85, 3.42)]
    assert repair_word_timing(words) is None


def test_repair_mostly_degenerate_rejected():
    # More than half the words need repair -> a collapsed, untrustworthy timeline.
    words = [("あ", 1.0, 1.0), ("い", 1.0, 1.0), ("う", 1.0, 1.0), ("え", 1.0, 1.5)]
    assert repair_word_timing(words, max_repair_frac=0.5) is None


def test_repair_cascade_of_zero_durations_stays_monotonic():
    # Several zero-duration tokens at one point (laughter/moaning chunks):
    # each is nudged forward by min_word_sec, keeping the list monotonic.
    words = [("ん", 1.0, 1.1), ("っ", 1.1, 1.1), ("っ", 1.1, 1.1), ("っ", 1.1, 1.1), ("え", 2.0, 2.5)]
    res = repair_word_timing(words, max_repair_frac=0.9)
    assert res is not None
    repaired, _ = res
    for (_, s0, e0), (_, s1, _) in zip(repaired, repaired[1:]):
        assert s0 < e0  # positive duration
        assert s1 >= s0  # non-decreasing starts
