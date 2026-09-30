"""Split recognized text into subtitle-sized cues.

Pure functions over text + optional word-level timestamps — no torch / audio
deps, so the splitting policy is unit-testable on its own. Used by the ja
worker after Qwen3-ASR (+ optional ForcedAligner): one ASR chunk may contain
several sentences (or several speakers' lines), these break it into cues of
at most ``max_chars`` / ``max_sec``, splitting at sentence punctuation first.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence

# Primary cue boundaries (sentence-ending punctuation, JA + ASCII).
_SENT_END = "。！？!?"
# Secondary boundaries, used only when a sentence still exceeds max_chars.
_SOFT_BREAK = "、，,…・：:"
_BREAK_CHARS = _SENT_END + _SOFT_BREAK

# A word with its absolute time range, as produced by the forced aligner.
Word = tuple[str, float, float]  # (text, start_sec, end_sec)

# start_at / end_at: char offset (into the whitespace-stripped text) -> seconds.
# They differ at word boundaries: a cue's start takes the *next* word's start,
# a cue's end takes the *previous* word's end (silence gaps between words
# belong to neither cue).
_Timeline = tuple[Callable[[float], float], Callable[[float], float]]


def split_cues(
    text: str,
    start: float,
    end: float,
    words: Sequence[Word] | None = None,
    max_chars: int = 24,
    max_sec: float = 8.0,
) -> list[dict]:
    """Split ``text`` (recognized for ``[start, end]``) into cue dicts.

    - Split at sentence-ending punctuation (。！？) first; short consecutive
      sentences are re-merged up to ``max_chars`` / ``max_sec``.
    - A sentence still longer than ``max_chars`` wraps at soft punctuation
      (、，…) near the limit, else hard-cuts at ``max_chars``.
    - Any cue still longer than ``max_sec`` is force-split near its middle.
    - ``words`` = word-level timestamps covering the same range (absolute
      seconds). When given and roughly consistent with ``text``, cue times
      come from the real word times; otherwise time is allocated
      proportionally by character count over ``[start, end]``.

    Returns ``[{"start", "end", "text"}, ...]`` with times clamped to
    ``[max(0, start), end]`` and monotonically non-decreasing.
    """
    t = "".join(str(text).split())
    if not t or end <= start:
        return []
    start, end = max(0.0, float(start)), float(end)

    timeline = _make_timeline(t, start, end, words)

    # Merge units into cues under the char/duration budgets, keeping each
    # piece's char offset for timing.
    pieces: list[tuple[str, int]] = []  # (text, char offset)
    buf, buf_off = "", 0
    for unit in _split_units(t, max_chars):
        cand = buf + unit
        too_long = len(cand) > max_chars
        too_slow = bool(buf) and (_dur(timeline, buf_off, buf_off + len(cand)) > max_sec)
        if buf and (too_long or too_slow):
            pieces.append((buf, buf_off))
            buf_off += len(buf)
            buf = unit
        else:
            buf = cand
    if buf:
        pieces.append((buf, buf_off))

    # A single overlong unit never hits the merge check above — force-split
    # anything still exceeding max_sec.
    split_pieces: list[tuple[str, int]] = []
    for ptext, poff in pieces:
        split_pieces.extend(_enforce_max_sec(ptext, poff, timeline, max_sec))

    cues = [_cue(ptext, poff, timeline, start, end) for ptext, poff in split_pieces]

    # Enforce monotonic, non-empty time ranges.
    prev = start
    for c in cues:
        c["start"] = max(c["start"], prev)
        c["end"] = max(c["end"], c["start"] + 0.05)
        prev = c["start"]
    return cues


def _dur(timeline: _Timeline, off0: float, off1: float) -> float:
    start_at, end_at = timeline
    return end_at(off1) - start_at(off0)


def _cue(text: str, off: int, timeline: _Timeline, lo: float, hi: float) -> dict:
    start_at, end_at = timeline
    s = min(max(start_at(off), lo), hi)
    e = min(max(end_at(off + len(text)), lo), hi)
    return {"start": round(s, 3), "end": round(e, 3), "text": text}


def _enforce_max_sec(text: str, off: int, timeline: _Timeline, max_sec: float) -> list[tuple[str, int]]:
    """Split ``text`` near its middle until every piece fits ``max_sec``
    (both halves recurse — the cut-off half may itself be too long)."""
    if len(text) <= 1 or _dur(timeline, off, off + len(text)) <= max_sec:
        return [(text, off)]
    cut = _break_near(text, len(text) // 2)
    return _enforce_max_sec(text[:cut], off, timeline, max_sec) + _enforce_max_sec(
        text[cut:], off + cut, timeline, max_sec
    )


def _break_near(text: str, target: int) -> int:
    """Cut position (1..len-1) with a break char just before it, closest to
    ``target``; hard-cut at ``target`` when there is none."""
    best, best_d = target, len(text)
    for i in range(1, len(text)):
        if text[i - 1] in _BREAK_CHARS and abs(i - target) < best_d:
            best, best_d = i, abs(i - target)
    return best


def _split_units(text: str, max_chars: int) -> list[str]:
    """Split text at sentence ends, then wrap overlong sentences."""
    sentences: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SENT_END:
            sentences.append(buf)
            buf = ""
    if buf:
        sentences.append(buf)

    units: list[str] = []
    for s in sentences:
        units.extend(_wrap(s, max_chars))
    return units


def _wrap(s: str, max_chars: int) -> list[str]:
    """Wrap one sentence: prefer the last soft-break char before the limit,
    hard-cut at the limit when there is none."""
    out: list[str] = []
    while len(s) > max_chars:
        cut = max(s.rfind(p, 0, max_chars + 1) for p in _SOFT_BREAK)
        cut = cut + 1 if cut > 0 else max_chars  # keep break char on the left
        out.append(s[:cut])
        s = s[cut:]
    if s:
        out.append(s)
    return out


def _make_timeline(text: str, start: float, end: float, words: Sequence[Word] | None) -> _Timeline:
    """(start_at, end_at) over char offsets into whitespace-stripped ``text``.

    Uses word timestamps when they roughly cover the text (aligner output may
    differ slightly from the ASR text); falls back to linear interpolation.
    """
    n = len(text)

    def linear(off: float) -> float:
        return start + (end - start) * min(max(off, 0.0), n) / n

    if not words:
        return linear, linear

    wtexts = ["".join(str(w[0]).split()) for w in words]
    total_w = sum(len(w) for w in wtexts)
    if total_w < max(1, int(0.5 * n)):
        return linear, linear  # word coverage too poor to be trusted

    cums = [0]
    for w in wtexts:
        cums.append(cums[-1] + len(w))
    scale = n / total_w if total_w else 1.0

    def word_time(off: float, boundary: Callable) -> float:
        wo = min(max(off, 0.0), n) / scale
        i = min(max(boundary(cums, wo) - 1, 0), len(words) - 1)
        w0, w1 = cums[i], cums[i + 1]
        ws, we = float(words[i][1]), float(words[i][2])
        frac = 0.0 if w1 <= w0 else min(1.0, (wo - w0) / (w1 - w0))
        return ws + frac * (we - ws)

    # start: at a char boundary the char begins a new word -> next word (bisect_right).
    # end: the last char of the cue ends a word -> that word (bisect_left).
    return (
        lambda off: word_time(off, bisect_right),
        lambda off: word_time(off, bisect_left),
    )
