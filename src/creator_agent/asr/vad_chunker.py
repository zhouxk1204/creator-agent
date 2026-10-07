"""Merge raw VAD speech intervals into ASR-sized chunks.

Pure functions over ``(start_sec, end_sec)`` tuples — no torch / audio deps,
so the chunking policy is unit-testable on its own. Used by the ja worker:
silero-vad emits fine-grained speech intervals, these merge short gaps into
utterance-like chunks and split oversized ones at their largest internal
silence.
"""

from __future__ import annotations

from collections.abc import Sequence


def speech_coverage(start: float, end: float, intervals: Sequence[tuple[float, float]]) -> float:
    """Fraction of ``[start, end]`` overlapped by speech ``intervals`` (0..1).

    Used to audit subtitle cues against a tight VAD pass: a cue covering
    almost no speech is an ASR hallucination or a misaligned word timeline.
    """
    if end <= start:
        return 0.0
    cov = sum(max(0.0, min(end, e) - max(start, s)) for s, e in intervals)
    return min(1.0, cov / (end - start))


def uncovered_ranges(
    intervals: Sequence[tuple[float, float]], covered: Sequence[tuple[float, float]], min_sec: float = 1.0
) -> list[tuple[float, float]]:
    """Speech ranges (from ``intervals``) with no overlap from ``covered``
    spans, merged and filtered to >= ``min_sec``. Surfaces spoken dialogue
    that ended up with no subtitle at all."""
    out: list[tuple[float, float]] = []
    for s, e in intervals:
        # Subtract covered spans from [s, e]; keep leftover pieces.
        pieces = [(s, e)]
        for cs, ce in covered:
            nxt = []
            for a, b in pieces:
                if ce <= a or cs >= b:
                    nxt.append((a, b))
                else:
                    if cs > a:
                        nxt.append((a, cs))
                    if ce < b:
                        nxt.append((ce, b))
            pieces = nxt
        out.extend(p for p in pieces if p[1] - p[0] >= min_sec)
    return out


def merge_speech_intervals(
    intervals: list[tuple[float, float]],
    max_gap: float = 0.3,
    max_chunk: float = 30.0,
    pad: float = 0.1,
    duration: float | None = None,
) -> list[tuple[float, float]]:
    """Merge speech intervals into chunks no longer than ``max_chunk``.

    - Consecutive intervals separated by <= ``max_gap`` seconds merge into one
      chunk (short breaths / pauses inside an utterance).
    - A merged chunk longer than ``max_chunk`` is split at its largest
      internal gap (recursively) so ASR never sees overly long audio.
    - Each final chunk gets ``pad`` seconds of context on both sides, clamped
      to ``[0, duration]`` when ``duration`` is given.
    """
    if not intervals:
        return []

    ivs = sorted((float(s), float(e)) for s, e in intervals if e > s)
    if not ivs:
        return []

    # Group into merged chunks, remembering constituent intervals so we can
    # split at internal gaps later.
    groups: list[list[tuple[float, float]]] = [[ivs[0]]]
    for s, e in ivs[1:]:
        if s - groups[-1][-1][1] <= max_gap:
            groups[-1].append((s, e))
        else:
            groups.append([(s, e)])

    chunks: list[tuple[float, float]] = []
    for group in groups:
        chunks.extend(_split_oversized(group, max_chunk))

    padded: list[tuple[float, float]] = []
    for s, e in chunks:
        s = max(0.0, s - pad)
        e = e + pad
        if duration is not None:
            e = min(float(duration), e)
        if e > s:
            padded.append((round(s, 3), round(e, 3)))
    return padded


def _split_oversized(group: list[tuple[float, float]], max_chunk: float) -> list[tuple[float, float]]:
    """Split one merged group at its largest internal gap while it exceeds
    ``max_chunk``. A single speech interval longer than max_chunk is kept as
    is (no silence to split at)."""
    start, end = group[0][0], group[-1][1]
    if end - start <= max_chunk or len(group) == 1:
        return [(start, end)]

    # Largest gap between consecutive intervals in the group.
    split_at = max(range(len(group) - 1), key=lambda i: group[i + 1][0] - group[i][1])
    left = group[: split_at + 1]
    right = group[split_at + 1 :]
    return _split_oversized(left, max_chunk) + _split_oversized(right, max_chunk)


# ---------------------------------------------------------------------------
# Targeted chunking (3-8s goal, hard cap) — the default ja-asr chunker.
# ---------------------------------------------------------------------------

# Chunk ``reason`` values (surfaced in the pipeline log and the run summary):
REASON_NORMAL = "normal"  # single utterance group within the target range
REASON_MERGED = "merged"  # several close groups packed into one chunk
REASON_SPLIT_NATURAL = "forced_split_natural"  # oversized group cut at an internal pause
REASON_SPLIT_HARD = "forced_split_hard"  # single >hard_max interval cut mid-speech


def build_chunks(
    intervals: list[tuple[float, float]],
    merge_gap: float = 0.3,
    target_min: float = 3.0,
    target_max: float = 8.0,
    hard_max: float = 15.0,
    pad: float = 0.1,
    duration: float | None = None,
) -> list[dict]:
    """Merge VAD speech intervals into ASR chunks of ~target_min..target_max
    seconds, never exceeding ``hard_max``.

    - Intervals separated by <= ``merge_gap`` merge into utterance *groups*
      (short breaths inside one line).
    - Consecutive groups pack into one chunk while the span stays within
      ``target_max``; an undersized chunk (< ``target_min``) may stretch past
      ``target_max`` up to ``hard_max`` to reach a reasonable length.
    - A single group longer than ``hard_max`` is force-split at the internal
      pause closest to ``target_max`` (recursively); a single *interval*
      longer than ``hard_max`` has no pause to split at and is hard-cut.

    Returns ``[{"start", "end", "reason", "segments"}, ...]`` — ``segments``
    is the number of source VAD intervals in the chunk (for logging).
    """
    ivs = sorted((float(s), float(e)) for s, e in intervals if e > s)
    if not ivs:
        return []

    # 1. Utterance groups (gap <= merge_gap; epsilon for float noise like
    # 1.7 - 1.4 = 0.30000000000000004).
    groups: list[list[tuple[float, float]]] = [[ivs[0]]]
    for s, e in ivs[1:]:
        if s - groups[-1][-1][1] <= merge_gap + 1e-9:
            groups[-1].append((s, e))
        else:
            groups.append([(s, e)])

    # 2. Force-split oversized groups -> pieces tagged with a split reason.
    pieces: list[tuple[list[tuple[float, float]], str | None]] = []
    for g in groups:
        pieces.extend(_split_group(g, target_max, hard_max))

    def span(ints: list[tuple[float, float]]) -> float:
        return ints[-1][1] - ints[0][0]

    # 3. Pack pieces into chunks under the target/hard budgets.
    chunks: list[dict] = []
    cur: list[tuple[float, float]] = []
    cur_reasons: set[str] = set()
    cur_groups = 0

    def flush() -> None:
        nonlocal cur, cur_reasons, cur_groups
        if not cur:
            return
        reason = (
            REASON_SPLIT_HARD
            if REASON_SPLIT_HARD in cur_reasons
            else REASON_SPLIT_NATURAL
            if REASON_SPLIT_NATURAL in cur_reasons
            else REASON_MERGED
            if cur_groups > 1
            else REASON_NORMAL
        )
        chunks.append({"start": cur[0][0], "end": cur[-1][1], "reason": reason, "segments": len(cur)})
        cur, cur_reasons, cur_groups = [], set(), 0

    for ints, split_reason in pieces:
        if cur and span(cur + ints) > target_max and (span(cur) >= target_min or span(cur + ints) > hard_max):
            flush()
        cur += ints
        cur_groups += 1
        if split_reason:
            cur_reasons.add(split_reason)
    flush()

    # 4. Pad + clamp, like merge_speech_intervals.
    out: list[dict] = []
    for c in chunks:
        s = max(0.0, c["start"] - pad)
        e = c["end"] + pad
        if duration is not None:
            e = min(float(duration), e)
        if e > s:
            out.append({**c, "start": round(s, 3), "end": round(e, 3)})
    return out


def _split_group(
    group: list[tuple[float, float]], target_max: float, hard_max: float
) -> list[tuple[list[tuple[float, float]], str | None]]:
    """Split one utterance group so every piece is <= ``hard_max``; pieces
    produced by splitting carry their split reason."""
    start, end = group[0][0], group[-1][1]
    if end - start <= hard_max:
        return [(group, None)]

    if len(group) == 1:
        # One continuous speech interval with no internal pause: hard-cut at
        # target_max and recurse on the remainder.
        (s, e) = group[0]
        cut = s + target_max
        left = [(s, cut)]
        right = [(cut, e)]
        return [(left, REASON_SPLIT_HARD)] + [
            (piece, REASON_SPLIT_HARD if reason is None else reason)
            for piece, reason in _split_group(right, target_max, hard_max)
        ]

    # Split at the internal pause whose midpoint is closest to start+target_max.
    cut_at = min(
        range(len(group) - 1),
        key=lambda i: abs((group[i][1] + group[i + 1][0]) / 2 - (start + target_max)),
    )
    left, right = group[: cut_at + 1], group[cut_at + 1 :]
    return [
        (piece, REASON_SPLIT_NATURAL if reason is None else reason)
        for piece, reason in _split_group(left, target_max, hard_max)
    ] + [
        (piece, REASON_SPLIT_NATURAL if reason is None else reason)
        for piece, reason in _split_group(right, target_max, hard_max)
    ]
