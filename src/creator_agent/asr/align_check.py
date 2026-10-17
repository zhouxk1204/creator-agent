"""Post-alignment quality gate for ForcedAligner word timestamps.

The aligner never raises on bad audio — it can also return plausible-looking
garbage (words locked onto a neighbouring utterance, degenerate timestamps).
Feeding those into the cue splitter produces subtitles whose times drift off
the actual speech. But it ALSO routinely emits a few harmless ZERO-DURATION
tokens (「は」「の」「っ」…) inside an otherwise accurate alignment — rejecting
the whole chunk over one such token forces an inaccurate proportional
fallback and is the main cause of drifted cue times.

So the flow is repair-then-reject: ``repair_word_timing`` patches the harmless
glitches (zero/short durations, sub-100ms overlaps), then ``check_alignment``
validates the result and REJECTS only genuinely untrustworthy alignments
(large overlaps, low coverage, out-of-window, abnormal durations). The caller
falls back to proportional cue times for rejected chunks, so
``resolve_overlaps`` stays a last-resort insurance for tiny genuine edge
overlaps.

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


def repair_word_timing(
    words: Sequence[Word] | None,
    min_word_sec: float = 0.05,
    max_overlap_sec: float = 0.1,
    max_repair_frac: float = 0.5,
) -> tuple[list[Word], int] | None:
    """Repair minor word-timing glitches so a good alignment survives the gate.

    The Qwen3 ForcedAligner emits ZERO-DURATION words (``start == end``) for
    short function morae it cannot place (「は」「の」「っ」「じゃ」…), plus the
    occasional tiny overlap between adjacent tokens. These are harmless — the
    token sits at the right spot, sandwiched between well-placed neighbours —
    but the strict monotonic / positive-duration gate would reject the WHOLE
    chunk over a single such token, forcing an inaccurate proportional
    fallback (the cause of badly-drifted cue times). Repair them instead:

    - a word starting slightly before its predecessor ends is clamped forward
      (overlap <= ``max_overlap_sec``);
    - a word shorter than ``min_word_sec`` (zero / negative duration included)
      is expanded to ``min_word_sec``.

    ``min_word_sec`` defaults to 0.05 — a realistic short-mora duration that
    also clears ``check_alignment``'s 0.02s ``ABNORMAL_DURATION`` floor with
    margin. (Expanding to exactly the gate's floor would re-trip that check:
    ``(ws + 0.02) - ws`` is a hair under 0.02 in floating point.)

    Returns ``(repaired_words, n_repaired)`` — monotonically non-decreasing,
    every duration >= ``min_word_sec`` — or ``None`` when the timing is
    genuinely broken and must NOT be trusted:

    - a word overlapping its predecessor by more than ``max_overlap_sec`` (it
      locked onto a neighbouring utterance; clamping would drag real cues off
      their audio), or
    - more than ``max_repair_frac`` of the words needed repair (a mostly-
      degenerate timeline, e.g. an alignment that collapsed onto one point —
      no better than the proportional fallback it would replace).

    ``check_alignment`` still runs on the repaired list afterwards: coverage
    and the out-of-window guard are unaffected by timing repair, so genuine
    garbage is rejected exactly as before.
    """
    if not words:
        return None
    out: list[Word] = []
    prev_end: float | None = None
    n_repaired = 0
    for wtext, ws, we in words:
        ws, we = float(ws), float(we)
        real_dur = we - ws  # original extent; ~0 marks an unplaced token
        fixed = False
        if prev_end is not None and ws < prev_end - 1e-3:
            # A REAL word (with extent) starting well before its predecessor
            # locked onto a neighbouring utterance — unrepairable. Zero-duration
            # tokens have no extent to overlap with; they are merely unplaced,
            # so clamp them without distrusting the whole chunk (this also keeps
            # a cluster of them — laughter/moaning — from tripping the guard as
            # the clamps cascade forward).
            if real_dur > 1e-3 and prev_end - ws > max_overlap_sec:
                return None
            ws = prev_end
            fixed = True
        if we < ws + min_word_sec:
            we = ws + min_word_sec
            fixed = True
        n_repaired += fixed
        out.append((wtext, ws, we))
        prev_end = we
    if n_repaired > max_repair_frac * len(words):
        return None
    return out, n_repaired


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
