"""Merge raw VAD speech intervals into ASR-sized chunks.

Pure functions over ``(start_sec, end_sec)`` tuples — no torch / audio deps,
so the chunking policy is unit-testable on its own. Used by the ja worker:
silero-vad emits fine-grained speech intervals, these merge short gaps into
utterance-like chunks and split oversized ones at their largest internal
silence.
"""

from __future__ import annotations


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
