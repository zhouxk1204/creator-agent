"""Split recognized text into subtitle-sized cues.

Pure functions over text + optional word-level timestamps — no torch / audio
deps, so the splitting policy is unit-testable on its own. Used by the ja
worker after Qwen3-ASR (+ optional ForcedAligner): one ASR chunk may contain
several sentences (or several speakers' lines), these break it into cues of
at most ``max_chars`` / ``max_sec``, splitting at sentence punctuation first.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

# Primary cue boundaries (sentence-ending punctuation, JA + ASCII).
_SENT_END = "。！？!?"
# Secondary boundaries, used only when a sentence still exceeds max_chars.
_SOFT_BREAK = "、，,…・：:"
_BREAK_CHARS = _SENT_END + _SOFT_BREAK

# A word with its absolute time range, as produced by the forced aligner.
Word = tuple[str, float, float]  # (text, start_sec, end_sec)

# start_at / end_at: char offset (into the whitespace-stripped text) -> seconds,
# plus a flag telling whether real word times back them. They differ at cue
# boundaries: a cue's start takes its first word's start, a cue's end takes
# its last word's end (silence gaps between words belong to neither cue).
_Timeline = tuple[Callable[[float], float], Callable[[float], float]]


def split_cues(
    text: str,
    start: float,
    end: float,
    words: Sequence[Word] | None = None,
    max_chars: int = 24,
    max_sec: float = 8.0,
    pause_sec: float = 0.6,
    speech: Sequence[tuple[float, float]] | None = None,
) -> list[dict]:
    """Split ``text`` (recognized for ``[start, end]``) into cue dicts.

    - Long pauses (>= ``pause_sec`` of silence between consecutive words,
      or between speech intervals in the fallback timeline) are HARD cue
      boundaries: a cue never straddles one, and a sentence containing one
      is cut there.
    - Split at sentence-ending punctuation (。！？) next; short consecutive
      sentences are re-merged up to ``max_chars`` / ``max_sec`` (never
      across a pause boundary).
    - A sentence still longer than ``max_chars`` wraps at soft punctuation
      (、，…) near the limit, else hard-cuts at ``max_chars``.
    - Any cue still longer than ``max_sec`` is force-split near its middle.
    - ``words`` = word-level timestamps covering the same range (absolute
      seconds). When given and roughly consistent with ``text``, cue times
      come from the real word times (cue start = its first word's start,
      cue end = its last word's end); otherwise time is allocated
      proportionally by character count. ``speech`` = VAD speech intervals
      (absolute seconds) makes that fallback speech-aware: text is spread
      over a compressed speech-only timeline, so cues start/end on utterance
      edges and never cover inter-utterance silence; without ``speech`` the
      fallback is plain linear over ``[start, end]``.

    Returns ``[{"start", "end", "text"}, ...]``. Word-timed cues keep their
    real word edges (they may fall slightly outside ``[start, end]`` when the
    aligner saw a wider window than the ASR chunk); proportional cues are
    clamped to ``[max(0, start), end]``. All cues are monotonically
    non-overlapping.
    """
    t = "".join(str(text).split())
    if not t or end <= start:
        return []
    start, end = max(0.0, float(start)), float(end)

    start_at, end_at, word_timed, pauses = _make_timeline(t, start, end, words, pause_sec, speech)
    timeline = (start_at, end_at)
    pause_set = set(pauses)

    # Split text into units (sentence punctuation + wrapping), each tagged
    # with its char offset; cut any unit that straddles a long pause at the
    # pause boundary.
    units: list[tuple[str, int]] = []  # (text, char offset)
    off = 0
    for u in _split_units(t, max_chars):
        last = 0
        for cut in (p - off for p in pauses if off < p < off + len(u)):
            units.append((u[last:cut], off + last))
            last = cut
        units.append((u[last:], off + last))
        off += len(u)

    # Merge units into cues under the char/duration budgets, keeping each
    # piece's char offset for timing; never merge across a pause boundary.
    pieces: list[tuple[str, int]] = []  # (text, char offset)
    buf, buf_off = "", 0
    for utext, uoff in units:
        cand = buf + utext
        too_long = len(cand) > max_chars
        too_slow = bool(buf) and (_dur(timeline, buf_off, buf_off + len(cand)) > max_sec)
        pause_break = bool(buf) and (uoff in pause_set)
        if buf and (too_long or too_slow or pause_break):
            pieces.append((buf, buf_off))
            buf, buf_off = utext, uoff
        else:
            buf = cand
    if buf:
        pieces.append((buf, buf_off))

    # A single overlong unit never hits the merge check above — force-split
    # anything still exceeding max_sec.
    split_pieces: list[tuple[str, int]] = []
    for ptext, poff in pieces:
        split_pieces.extend(_enforce_max_sec(ptext, poff, timeline, max_sec))

    cues = [_cue(ptext, poff, timeline, start, end, clamp=not word_timed) for ptext, poff in split_pieces]

    # Enforce monotonic, non-overlapping, non-empty time ranges.
    prev_end = 0.0
    for c in cues:
        c["start"] = max(c["start"], prev_end)
        c["end"] = max(c["end"], c["start"] + 0.05)
        prev_end = c["end"]
    return cues


def resolve_overlaps(segments: list[dict], min_dur: float = 0.05) -> list[dict]:
    """De-overlap cue time ranges ACROSS chunks; returns cues sorted by time.

    split_cues() keeps cues within one ASR chunk monotonic, but word-timed
    cue edges may fall outside their chunk (the aligner sees a window padded
    beyond the chunk), so the last cue of chunk N can overlap the first cue
    of chunk N+1 — diarized simultaneous speech does the same. Walk the cues
    in time order and split each overlap at its midpoint, keeping every cue
    at least ``min_dur`` long; when two cues are too short to share, the
    later one starts where the previous ends (any new collision that creates
    is resolved by the next loop iteration).
    """
    segs = sorted(segments, key=lambda s: (s["start"], s["end"]))
    for i in range(1, len(segs)):
        prev, cur = segs[i - 1], segs[i]
        if prev["end"] <= cur["start"]:
            continue
        lo, hi = prev["start"] + min_dur, cur["end"] - min_dur
        if lo < hi:
            boundary = min(max((prev["end"] + cur["start"]) / 2, lo), hi)
            prev["end"] = cur["start"] = round(boundary, 3)
        else:
            cur["start"] = prev["end"]
            if cur["end"] < cur["start"] + min_dur:
                cur["end"] = cur["start"] + min_dur
    return segs


def _dur(timeline: _Timeline, off0: float, off1: float) -> float:
    start_at, end_at = timeline
    return end_at(off1) - start_at(off0)


def _cue(text: str, off: int, timeline: _Timeline, lo: float, hi: float, clamp: bool) -> dict:
    start_at, end_at = timeline
    s, e = start_at(off), end_at(off + len(text))
    if clamp:  # proportional times are only meaningful inside [lo, hi]
        s = min(max(s, lo), hi)
        e = min(max(e, lo), hi)
    else:  # word times are real; keep edges that fall outside the chunk
        s, e = max(s, 0.0), max(e, 0.0)
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


def match_words_to_text(text: str, words: Sequence[Word]) -> tuple[list[int | None], int]:
    """Walk ``text`` and ``words`` in order, mapping each text char to the
    index of the word that produced it. Returns ``(char_word, matched)``:
    ``char_word[i]`` is the word index covering text char ``i`` (None for
    unmatched chars like dropped punctuation), ``matched`` the total number
    of matched chars. Shared by the cue timeline and the alignment quality
    gate (coverage check) so both measure matchability identically."""
    n = len(text)
    char_word: list[int | None] = [None] * n
    cursor = 0
    matched = 0
    for wi, w in enumerate(words):
        wt = "".join(str(w[0]).split())
        if not wt:
            continue
        j = text.find(wt, cursor)
        if j < 0:
            continue  # ASR/aligner text mismatch on this word; skip it
        for p in range(j, j + len(wt)):
            char_word[p] = wi
        cursor = j + len(wt)
        matched += len(wt)
    return char_word, matched


def _make_timeline(
    text: str,
    start: float,
    end: float,
    words: Sequence[Word] | None,
    pause_sec: float = 0.6,
    speech: Sequence[tuple[float, float]] | None = None,
) -> tuple[Callable[[float], float], Callable[[float], float], bool, list[int]]:
    """(start_at, end_at, word_timed, pause_breaks) over char offsets into ``text``.

    Aligner words, concatenated, reproduce the ASR text in order but usually
    WITHOUT punctuation (and occasionally tokenized differently). So instead
    of scaling char offsets (which drifts with every unmatched char and lands
    cue boundaries inside the NEXT sentence's word), walk both sequences in
    order and map each text char to the exact word that produced it: a cue's
    start is its first word's start, its end is its last word's end.

    ``pause_breaks`` = char offsets where the silence between two consecutive
    matched words (or, in the speech fallback, between two speech intervals)
    reaches ``pause_sec`` (offset = first char of the later piece, so leading
    punctuation stays with the left piece).

    Fallback order: word timeline -> speech-interval timeline (``speech``) ->
    plain linear interpolation over [start, end].
    """
    n = len(text)

    def linear(off: float) -> float:
        return start + (end - start) * min(max(off, 0.0), n) / n

    if words:
        char_word, matched = match_words_to_text(text, words)
        if matched >= max(1, int(0.5 * n)):
            return _word_timeline(text, words, char_word, pause_sec)
    if speech:
        st = _speech_timeline(text, start, end, speech, pause_sec)
        if st is not None:
            return st
    return linear, linear, False, []


def _word_timeline(
    text: str, words: Sequence[Word], char_word: list[int | None], pause_sec: float
) -> tuple[Callable[[float], float], Callable[[float], float], bool, list[int]]:
    """Word-timestamp timeline: cue edges take the real word edges."""
    n = len(text)

    covered = [wi for wi in char_word if wi is not None]
    first_w, last_w = covered[0], covered[-1]

    def start_at(off: float) -> float:
        # First word char at/after the offset; trailing punctuation-only
        # tails take the last word's end.
        for p in range(int(min(max(off, 0.0), n)), n):
            wi = char_word[p]
            if wi is not None:
                return float(words[wi][1])
        return float(words[last_w][2])

    def end_at(off: float) -> float:
        # Last word char before the offset; leading punctuation-only heads
        # take the first word's start.
        for p in range(int(min(max(off, 0.0), n)) - 1, -1, -1):
            wi = char_word[p]
            if wi is not None:
                return float(words[wi][2])
        return float(words[first_w][1])

    # Hard cue boundaries: silence >= pause_sec between consecutive matched
    # words. The offset is the first char of the later word, so punctuation
    # the aligner dropped stays attached to the left piece.
    pauses: list[int] = []
    seq = list(dict.fromkeys(covered))  # matched word indices, in order
    for a, b in zip(seq, seq[1:]):
        if float(words[b][1]) - float(words[a][2]) >= pause_sec:
            pauses.append(char_word.index(b))

    return start_at, end_at, True, pauses


def _speech_timeline(
    text: str,
    start: float,
    end: float,
    speech: Sequence[tuple[float, float]],
    pause_sec: float,
) -> tuple[Callable[[float], float], Callable[[float], float], bool, list[int]] | None:
    """Speech-aware proportional timeline (no word timestamps available).

    Text chars are spread over a COMPRESSED speech-only timeline: the speech
    intervals inside [start, end] concatenated with the silence squeezed out.
    A cue's start/end therefore always land inside an utterance — subtitles
    never hang over inter-utterance silence, and cue boundaries snap to
    utterance edges (cue end = left interval's end, next cue's start = right
    interval's start). Gaps >= ``pause_sec`` between intervals become hard
    cue boundaries at the char offset the gap maps to.

    Returns None when no speech interval overlaps [start, end] (caller falls
    back to plain linear).
    """
    n = len(text)
    ivs: list[tuple[float, float]] = []
    for s, e in sorted((float(s), float(e)) for s, e in speech):
        a, b = max(s, start), min(e, end)
        if b > a:
            ivs.append((a, b))
    if not ivs:
        return None

    cum = [0.0]  # compressed (speech-only) seconds elapsed before each interval
    for a, b in ivs:
        cum.append(cum[-1] + (b - a))
    total = cum[-1]
    last = len(ivs) - 1

    def _map(c: float, from_left: bool) -> float:
        # from_left=True (cue start): a position exactly on an interval
        # boundary maps into the RIGHT interval's start; False (cue end):
        # into the LEFT interval's end. The gap stays subtitle-free.
        for i, (a, b) in enumerate(ivs):
            if c < cum[i + 1] or (not from_left and c <= cum[i + 1]) or i == last:
                return a + min(max(c - cum[i], 0.0), b - a)
        return ivs[-1][1]  # unreachable (i == last catches all)

    def start_at(off: float) -> float:
        return _map(total * min(max(off, 0.0), n) / n, from_left=True)

    def end_at(off: float) -> float:
        return _map(total * min(max(off, 0.0), n) / n, from_left=False)

    pauses: list[int] = []
    for i in range(last):
        if ivs[i + 1][0] - ivs[i][1] >= pause_sec:
            off = round(n * cum[i + 1] / total)
            if 0 < off < n:
                pauses.append(off)

    return start_at, end_at, False, pauses


def cut_repeat_tail(text: str, min_repeats: int = 3, min_span: int = 6) -> str:
    """Truncate a degenerate repetition tail (ASR hallucination on music /
    silence): the same unit repeated >= ``min_repeats`` times at the very end
    of the text, spanning >= ``min_span`` chars. Returns the text cut back to
    one occurrence of the unit; unchanged when there is no such tail.

    Whitespace is stripped first (JA ASR output carries none anyway). Only
    tails are cut — stutters at the start of a line (「ねねねね…」) are real
    speech and are left alone.
    """
    t = "".join(str(text).split())
    n = len(t)
    best = n
    for p in range(1, n // min_repeats + 1):
        unit = t[n - p :]
        k = 1
        while (k + 1) * p <= n and t[n - (k + 1) * p : n - k * p] == unit:
            k += 1
        if k >= min_repeats and k * p >= min_span:
            best = min(best, n - (k - 1) * p)
    return t[:best]
