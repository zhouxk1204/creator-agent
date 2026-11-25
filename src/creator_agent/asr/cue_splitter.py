"""Split recognized text into subtitle-sized cues.

Pure functions over text + optional word-level timestamps — no torch / audio
deps, so the splitting policy is unit-testable on its own. Used by the ja
worker after Qwen3-ASR (+ optional ForcedAligner): one ASR chunk may contain
several sentences (or several speakers' lines), these break it into cues of
at most ``max_chars`` / ``max_sec``, splitting at sentence punctuation first.

Proportional-fallback pause boundaries (no aligner words) are snapped to a
nearby punctuation edge — or nudged off small kana when none is close — so a
pause cut never lands inside a word. ``merge_fragments_and_fix_duration``
then folds the 1-2 char fragments that survive and stretches flash cues.
Finished cues are stripped of leading punctuation/whitespace
(``_strip_cue_head``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

# Primary cue boundaries (sentence-ending punctuation, JA + ASCII).
_SENT_END = "。！？!?"
# Secondary boundaries, used only when a sentence still exceeds max_chars.
_SOFT_BREAK = "、，,…・：:"
_BREAK_CHARS = _SENT_END + _SOFT_BREAK

# How far (chars) an estimated proportional pause offset may travel to reach a
# punctuation boundary before we give up and drop that pause cut.
_PAUSE_SNAP_WINDOW = 3

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

    # Strip leading punctuation/whitespace from each finished cue; advance the
    # offset so timing anchors on the first content char. A cue reduced to
    # nothing was pure residual punctuation (no spoken content) and is dropped.
    cues: list[dict] = []
    for ptext, poff in split_pieces:
        cleaned, lead = _strip_cue_head(ptext)
        if not cleaned:
            continue
        cues.append(_cue(cleaned, poff + lead, timeline, start, end, clamp=not word_timed))

    # Enforce monotonic, non-overlapping, non-empty time ranges.
    prev_end = 0.0
    for c in cues:
        c["start"] = max(c["start"], prev_end)
        c["end"] = max(c["end"], c["start"] + 0.05)
        prev_end = c["end"]
    return cues


def merge_fragments_and_fix_duration(
    cues: list[dict],
    max_chars: int = 30,
    min_sec: float = 0.4,
    max_gap_merge_sec: float = 1.5,
) -> list[dict]:
    """Post-process split_cues() output into readable cues.

    1. A 1-2 char cue is almost always a word fragment the aligner cut in the
       middle ("お義姉|さん", "し|た"), not a real line: fold it into the
       previous cue when that cue is still short enough. Fragments left at the
       head (no previous cue) are folded into the next one instead.
    2. A cue shorter than ``min_sec`` (0.05-0.3s flashes produced by degenerate
       word times) gets its end stretched into the following silence — never
       past the next cue — so it is physically readable. Text is untouched.

    Deliberately no text rewriting: only boundaries and fragment joins, so the
    review json still maps cue text to one ASR chunk.
    """
    if not cues:
        return []
    out: list[dict] = []
    for cue in cues:
        c = dict(cue)  # keep extra keys (diag/speaker) of the surviving piece
        c["start"], c["end"], c["text"] = float(cue["start"]), float(cue["end"]), str(cue["text"])
        t = c["text"]
        if out and len(t) <= 2 and len(out[-1]["text"]) + len(t) <= max_chars + 8:
            prev = out[-1]
            # A complete line ends a sentence; the next 2-char cue is then a
            # different utterance (「未来ラジオ!」+「ああ」), not a fragment.
            if prev["text"][-1] not in _SENT_END and c["start"] - prev["end"] <= max_gap_merge_sec:
                prev["text"] += t
                prev["end"] = max(prev["end"], c["end"])
                if isinstance(prev.get("diag"), dict):
                    prev["diag"]["fragment_merged"] = int(prev["diag"].get("fragment_merged", 0)) + 1
                continue
        out.append(c)
    # Head fragments (nothing before them) merge forward.
    if len(out) >= 2 and len(out[0]["text"]) <= 2 and out[0]["end"] - out[0]["start"] < min_sec:
        out[1]["text"] = out[0]["text"] + out[1]["text"]
        out[1]["start"] = out[0]["start"]
        out.pop(0)

    for i, c in enumerate(out):
        if c["end"] - c["start"] >= min_sec:
            continue
        limit = out[i + 1]["start"] if i + 1 < len(out) else c["start"] + min_sec
        new_end = min(c["start"] + min_sec, max(c["start"] + 0.05, limit - 0.02))
        c["end"] = max(c["end"], new_end)
    prev_end = 0.0
    for c in out:
        c["start"] = round(max(c["start"], prev_end), 3)
        c["end"] = round(max(c["end"], c["start"] + 0.05), 3)
        prev_end = c["end"]
    return out


def dedupe_cue_boundaries(cues: list[dict], max_overlap: int = 6, max_gap: float = 0.3) -> tuple[list[dict], int]:
    """Trim a word repeated on both sides of a cue boundary.

    Adjacent ASR chunks overlap by ~0.2s (and the aligner window is padded),
    so the same word often ends chunk N and starts chunk N+1 — the subtitle
    then reads 「…久しぶりにふた」/「ふたりで映画でも…」. When the cues are
    contiguous (gap ≤ ``max_gap``) and prev's tail equals cur's head (2 chars
    up to ``max_overlap``), the tail is trimmed. Only the text is touched,
    never the times, and only at the boundary between two cues.
    """
    out = [dict(c) for c in cues]
    fixed = 0
    for i in range(1, len(out)):
        prev, cur = out[i - 1], out[i]
        if cur["start"] - prev["end"] > max_gap:
            continue
        limit = min(max_overlap, len(prev["text"]) - 1, len(cur["text"]) - 1)
        for n in range(limit, 1, -1):
            if prev["text"][-n:] == cur["text"][:n]:
                prev["text"] = prev["text"][:-n]
                if isinstance(prev.get("diag"), dict):
                    prev["diag"]["boundary_dup_trimmed"] = n
                fixed += 1
                break
    return out, fixed


def resolve_overlaps(segments: list[dict], min_dur: float = 0.05) -> list[dict]:
    """De-overlap cue time ranges ACROSS chunks; returns cues sorted by time.

    split_cues() keeps cues within one ASR chunk monotonic, but word-timed
    cue edges may fall outside their chunk (the aligner sees a window padded
    beyond the chunk), so the last cue of chunk N can overlap the first cue
    of chunk N+1 — diarized simultaneous speech does the same. Walk the cues
    in time order: a cue fully contained in the previous one (with much less
    text, e.g. the repeated tail word at a chunk boundary) is dropped, other
    overlaps are split at their midpoint, keeping every cue at least ``min_dur``
    long; when two cues are too short to share, the later one starts where the
    previous ends (any new collision is resolved by the next iteration).
    """
    segs = sorted(segments, key=lambda s: (s["start"], s["end"]))
    i = 1
    while i < len(segs):
        prev, cur = segs[i - 1], segs[i]
        if prev["end"] <= cur["start"]:
            i += 1
            continue
        # cur lies entirely inside prev: almost always the tail words of chunk
        # N repeated by chunk N+1's opening (「あっち」 inside 「あっちへ行って
        # なさい…」). Splitting at the midpoint used to shrink the 4.7s cue to
        # a 0.3s flash and leave the rest of the sentence untimed; keep the
        # longer cue untouched and drop the duplicate instead.
        if cur["end"] <= prev["end"]:
            pdur = prev["end"] - prev["start"]
            cdur = cur["end"] - cur["start"]
            if len(cur["text"]) < len(prev["text"]) and cdur < 0.5 * pdur:
                if isinstance(prev.get("diag"), dict):
                    prev["diag"]["contained_dup_dropped"] = cur["text"][:12]
                segs.pop(i)
                continue
        lo, hi = prev["start"] + min_dur, cur["end"] - min_dur
        if lo < hi:
            boundary = min(max((prev["end"] + cur["start"]) / 2, lo), hi)
            prev["end"] = cur["start"] = round(boundary, 3)
        else:
            cur["start"] = prev["end"]
            if cur["end"] < cur["start"] + min_dur:
                cur["end"] = cur["start"] + min_dur
        i += 1
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


def _strip_cue_head(text: str) -> tuple[str, int]:
    """Return (text without leading break-chars/whitespace, chars stripped).

    Only break-chars (。！？!?、，,…・：:) and whitespace are stripped — never
    kana, small kana (っゃゅょ…) or the long-vowel mark ー, which are spoken
    content and must be kept (and flagged for review elsewhere if suspect).
    """
    i = 0
    while i < len(text) and (text[i] in _BREAK_CHARS or text[i].isspace()):
        i += 1
    return text[i:], i


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
            return _word_timeline(text, words, char_word, pause_sec, speech)
    if speech:
        st = _speech_timeline(text, start, end, speech, pause_sec)
        if st is not None:
            return st
    return linear, linear, False, []


def _word_timeline(
    text: str,
    words: Sequence[Word],
    char_word: list[int | None],
    pause_sec: float,
    speech: Sequence[tuple[float, float]] | None = None,
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
    #
    # A word gap is only trusted when the audio agrees: degenerate aligner
    # times (zero-length words like 「し」0.00s then 「た」+0.7s) invent gaps
    # that are actually continuous speech, and cutting there splits a word
    # ("し|たからね" -> a 0.3s flash cue). ``speech`` (VAD intervals) vetoes
    # any gap that still contains detected speech.
    pauses: list[int] = []
    seq = list(dict.fromkeys(covered))  # matched word indices, in order
    for a, b in zip(seq, seq[1:]):
        gap_lo, gap_hi = float(words[a][2]), float(words[b][1])
        if gap_hi - gap_lo < pause_sec:
            continue
        if speech and _speech_within(gap_lo, gap_hi, speech):
            continue
        pauses.append(char_word.index(b))

    return start_at, end_at, True, pauses


def _speech_within(lo: float, hi: float, speech: Sequence[tuple[float, float]], min_cov: float = 0.15) -> bool:
    """True when ``speech`` covers more than ``min_cov`` seconds inside [lo, hi]."""
    return any(min(hi, e) - max(lo, s) > min_cov for s, e in speech)


_SMALL_KANA = "ぁぃぅぇぉゃゅょゎァィゥェォャュョヮっッ"  # never start/end a cue on these


def _nudge_cut(text: str, cut: int) -> int:
    """Move an estimated cut off a mid-word position that no punctuation
    marks: never put a small kana / long-vowel mark alone on either side."""
    n = len(text)
    p = max(1, min(cut, n - 1))
    for _ in range(2):
        if p < n - 1 and (text[p] in _SMALL_KANA or text[p] == "ー"):
            p += 1
        elif p > 1 and (text[p - 1] in _SMALL_KANA or text[p - 1] == "ー"):
            p -= 1
        else:
            break
    return max(1, min(p, n - 1))


def _snap_offsets_to_breaks(text: str, offsets: Sequence[int], window: int) -> tuple[list[int], list[dict]]:
    """Snap each estimated pause offset to the best safe cut position.

    A cut at ``p`` splits ``text`` so the right piece starts at ``text[p]``. It
    is punctuation-safe when ``text[p-1]`` is a break char (punctuation never
    occurs inside a word). For every estimated offset ``est`` we search
    ``[est-window, est+window]`` for such positions and pick the best by
    (distance to est, sentence-end preferred, position).

    When no punctuation is in reach the pause STILL cuts — the pause is real
    audio information, and merging the two sides would make one cue straddle
    the silence — but the cut is nudged off small kana / ー so it does not
    shatter a word's tail (``action="cut"``). The 1-2 char fragments that
    remain are folded back by ``merge_fragments_and_fix_duration``.

    Returns ``(offsets, records)``; each record is
    ``{"raw", "snapped", "action", "punct", "distance"}``.
    """
    n = len(text)
    snapped: list[int] = []
    records: list[dict] = []
    for est in offsets:
        best: int | None = None
        best_key: tuple[int, int, int] | None = None
        lo = max(1, est - window)
        hi = min(n - 1, est + window)
        for p in range(lo, hi + 1):
            if text[p - 1] not in _BREAK_CHARS:
                continue
            key = (abs(p - est), 0 if text[p - 1] in _SENT_END else 1, p)
            if best_key is None or key < best_key:
                best_key, best = key, p
        if best is None:
            best = _nudge_cut(text, est)
            snapped.append(best)
            records.append({"raw": est, "snapped": best, "action": "cut", "punct": None, "distance": best - est})
            continue
        snapped.append(best)
        records.append(
            {
                "raw": est,
                "snapped": best,
                "action": "keep" if best == est else "snap",
                "punct": text[best - 1],
                "distance": best - est,
            }
        )
    return sorted(set(snapped)), records


def _raw_pause_offsets(
    n: int, ivs: Sequence[tuple[float, float]], cum: Sequence[float], total: float, pause_sec: float
) -> list[int]:
    """Raw character-proportional pause offsets (unsnapped).

    Factored out so diagnostics can introspect raw -> snapped. ``cum[i]`` =
    compressed speech-only seconds before interval ``i``; ``total`` = cum[-1].
    """
    offsets: list[int] = []
    for i in range(len(ivs) - 1):
        if ivs[i + 1][0] - ivs[i][1] >= pause_sec:
            off = round(n * cum[i + 1] / total)
            if 0 < off < n:
                offsets.append(off)
    return offsets


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
    cue boundaries, whose char offset is snapped to a nearby punctuation edge
    (or dropped when no punctuation is close — see ``_snap_offsets_to_breaks``).

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

    # Raw character-proportional pause offsets, then snap each to a nearby
    # punctuation boundary (or drop it) so no cut lands inside a word.
    raw = _raw_pause_offsets(n, ivs, cum, total, pause_sec)
    pauses, _records = _snap_offsets_to_breaks(text, raw, _PAUSE_SNAP_WINDOW)

    return start_at, end_at, False, pauses


def cut_repeat_tail(
    text: str,
    min_repeats: int = 3,
    min_span: int = 6,
    speech_dur: float | None = None,
    max_cps: float = 0.0,
) -> str:
    """Truncate a degenerate repetition tail (ASR hallucination on music /
    silence): the same unit repeated >= ``min_repeats`` times at the very end
    of the text, spanning >= ``min_span`` chars. Returns the text cut back to
    one occurrence of the unit; unchanged when there is no such tail.

    Whitespace is stripped first (JA ASR output carries none anyway). Only
    tails are cut — stutters at the start of a line (「ねねねね…」) are real
    speech and are left alone.

    Speaking-rate guard: when ``max_cps`` > 0 and ``speech_dur`` (seconds of
    actual speech in the chunk, from the tight VAD pass) is given, the tail is
    only cut when the overall rate len(text)/speech_dur exceeds ``max_cps``
    chars/sec — a hallucination loop produces far more text than the audio
    could contain, while real 「うんうんうん」-style back-channels at normal
    speed are kept. ``max_cps`` = 0 restores the old always-cut behavior.
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
    if best == n:
        return t
    if max_cps > 0 and speech_dur is not None and n / max(speech_dur, 0.5) <= max_cps:
        return t
    return t[:best]
