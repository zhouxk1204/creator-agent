"""Speaker-turn aware chunk building.

Pure functions over VAD speech intervals + per-interval speaker labels — no
torch / audio deps, so unit-testable on its own. Used by the ja worker after
CAM++ embedding clustering: consecutive intervals from the same speaker merge
into ASR chunks; a speaker change always starts a new chunk, so one subtitle
never mixes two people's lines.
"""

from __future__ import annotations

try:  # main env (creator_agent installed)
    from creator_agent.asr.vad_chunker import merge_speech_intervals
except ImportError:  # ja_worker env: same directory is on sys.path
    from vad_chunker import merge_speech_intervals  # type: ignore[no-redef]


def smooth_labels(labels: list[int]) -> list[int]:
    """Flip singleton mislabels: an interval whose neighbours on both sides
    share a different label is almost certainly a clustering error (short
    back-channel like 「うん」 often embeds ambiguously). Two passes catch
    adjacent singletons."""
    out = list(labels)
    for _ in range(2):
        flipped = out[:]
        for i in range(1, len(out) - 1):
            if out[i - 1] == out[i + 1] != out[i]:
                flipped[i] = out[i - 1]
        if flipped == out:
            break
        out = flipped
    return out


def build_speaker_chunks(
    intervals: list[tuple[float, float]],
    labels: list[int],
    max_gap: float = 0.3,
    max_chunk: float = 15.0,
    pad: float = 0.1,
    duration: float | None = None,
) -> list[tuple[float, float, int]]:
    """Merge consecutive same-speaker intervals into ASR chunks.

    A change of ``labels`` always closes the current chunk (even mid-gap);
    within one speaker's run, the usual gap/oversize rules of
    ``merge_speech_intervals`` apply. Returns ``[(start, end, label), ...]``
    sorted by time, padded and clamped like ``merge_speech_intervals``.
    """
    if not intervals or len(intervals) != len(labels):
        return []

    chunks: list[tuple[float, float, int]] = []
    run_label = labels[0]
    run = [intervals[0]]

    def flush() -> None:
        for s, e in merge_speech_intervals(run, max_gap=max_gap, max_chunk=max_chunk, pad=0.0):
            chunks.append((s, e, run_label))

    for iv, lab in zip(intervals[1:], labels[1:]):
        if lab == run_label:
            run.append(iv)
        else:
            flush()
            run_label, run = lab, [iv]
    flush()

    out: list[tuple[float, float, int]] = []
    for s, e, lab in chunks:
        s = max(0.0, s - pad)
        e = e + pad
        if duration is not None:
            e = min(float(duration), e)
        if e > s:
            out.append((round(s, 3), round(e, 3), lab))
    return out


def speaker_names(labels: list[int]) -> dict[int, str]:
    """Map cluster ids to display names (話者A, 話者B, ...) in order of first
    appearance, so names stay stable across chunks of one video."""
    names: dict[int, str] = {}
    for lab in labels:
        if lab not in names:
            names[lab] = f"話者{chr(ord('A') + len(names))}"
    return names
