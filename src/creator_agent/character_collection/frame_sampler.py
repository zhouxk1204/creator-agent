"""Decide how many candidate frames a scene yields and at which timestamps.

Initial sampling rules (configurable, tuned for animation):

    duration <= 1s   -> 1 frame
    1s < duration <= 4s  -> 3 frames
    4s < duration <= 8s  -> 4 frames
    duration > 8s    -> 5 frames

Sampling is uniform INSIDE the scene with a small margin at both ends to
avoid the transition frames near the cut points.
"""

from __future__ import annotations

# Seconds trimmed from each end of a scene before sampling (never more than
# 10% of the scene duration, so very short scenes still get their midpoint).
EDGE_MARGIN_S = 0.15

# (upper duration bound in seconds, candidate count); last entry is the fallback.
COUNT_RULES: list[tuple[float, int]] = [
    (1.0, 1),
    (4.0, 3),
    (8.0, 4),
]
MAX_COUNT = 5


def planned_count(duration: float) -> int:
    """Candidate frame count for a scene of ``duration`` seconds."""
    for upper, count in COUNT_RULES:
        if duration <= upper:
            return count
    return MAX_COUNT


def sample_times(start: float, end: float) -> list[float]:
    """Uniform sample timestamps (seconds) inside [start, end).

    The scene is divided into ``planned_count`` equal cells (after trimming
    the edge margins) and each sample sits at the middle of its cell, so
    samples never land on the cut frames themselves.
    """
    if end <= start:
        return [start]
    duration = end - start
    n = planned_count(duration)
    margin = min(EDGE_MARGIN_S, duration * 0.1)
    lo, hi = start + margin, end - margin
    if n == 1 or hi <= lo:
        return [(start + end) / 2]
    step = (hi - lo) / n
    return [lo + step * (i + 0.5) for i in range(n)]
