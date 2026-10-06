"""Post-alignment quality gate for ForcedAligner word timestamps.

The aligner never raises on bad audio — it can also return plausible-looking
garbage (words locked onto a neighbouring utterance, degenerate timestamps).
Feeding those into the cue splitter produces subtitles whose times drift off
the actual speech, and the old midpoint de-overlap then "repaired" the
overlap symmetrically, corrupting BOTH cues. This module instead REJECTS
untrustworthy alignments up front (caller falls back to proportional cue
times for that chunk), so ``resolve_overlaps`` stays a last-resort insurance
for tiny genuine edge overlaps.

Pure functions, no heavy deps — unit-testable on its own. Imported by the
ja worker through the same sys.path shim as cue_splitter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

try:  # main env (creator_agent installed)
    from creator_agent.asr.cue_splitter import Word, match_words_to_text
except ImportError:  # ja_worker env: same directory is on sys.path
    from cue_splitter import Word, match_words_to_text  # type: ignore[no-redef]

# Failure reasons (stable strings — they appear in logs and the run summary).
NO_WORDS = "NO_WORD_TIMESTAMPS"
LOW_COVERAGE = "LOW_COVERAGE"
OUT_OF_WINDOW = "OUT_OF_WINDOW"
INVALID_TIMING = "INVALID_TIMING"
ABNORMAL_DURATION = "ABNORMAL_DURATION"


@dataclass
class AlignCheck:
    ok: bool
    reason: str = ""  # one of the constants above when ok is False
    coverage: float = 0.0  # fraction of text chars matched to a word
    detail: str = ""  # human-readable specifics for the log line


def check_alignment(
    words: Sequence[Word] | None,
    text: str,
    chunk_start: float,
    chunk_end: float,
    pad: float,
    min_coverage: float = 0.5,
    min_word_sec: float = 0.02,
    max_word_sec: float = 3.0,
) -> AlignCheck:
    """Validate ``words`` (absolute seconds) against the chunk they came from.

    Checks, in order (first failure wins):
      1. any words at all (NO_WORD_TIMESTAMPS)
      2. enough of the ASR text matched to words (LOW_COVERAGE)
      3. every word inside [chunk_start - pad, chunk_end + pad] (OUT_OF_WINDOW)
      4. start < end and monotonically non-decreasing (INVALID_TIMING)
      5. every word duration within [min_word_sec, max_word_sec] (ABNORMAL_DURATION)

    ``pad`` must equal the window padding the aligner actually saw — words are
    allowed to spill that far beyond the chunk (recovering VAD-clipped
    onsets/tails), but no further.
    """
    if not words:
        return AlignCheck(False, NO_WORDS)

    t = "".join(str(text).split())
    _, matched = match_words_to_text(t, words)
    coverage = matched / max(1, len(t))
    if coverage < min_coverage:
        return AlignCheck(False, LOW_COVERAGE, coverage, f"{coverage:.0%} < {min_coverage:.0%}")

    lo, hi = chunk_start - pad, chunk_end + pad
    for wtext, ws, we in words:
        if ws < lo - 1e-3 or we > hi + 1e-3:
            return AlignCheck(
                False,
                OUT_OF_WINDOW,
                coverage,
                f'word "{wtext}" {ws:.2f}-{we:.2f} outside allowed {lo:.2f}-{hi:.2f}',
            )

    prev_end: float | None = None
    for wtext, ws, we in words:
        if ws >= we:
            return AlignCheck(False, INVALID_TIMING, coverage, f'word "{wtext}" start {ws:.2f} >= end {we:.2f}')
        if prev_end is not None and ws < prev_end - 1e-3:
            return AlignCheck(
                False,
                INVALID_TIMING,
                coverage,
                f'word "{wtext}" starts {ws:.2f} before previous word ends {prev_end:.2f}',
            )
        prev_end = we

    for wtext, ws, we in words:
        d = we - ws
        if d < min_word_sec or d > max_word_sec:
            return AlignCheck(False, ABNORMAL_DURATION, coverage, f'word "{wtext}" duration {d:.3f}s')

    return AlignCheck(True, coverage=coverage)
