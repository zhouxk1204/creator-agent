from __future__ import annotations

from creator_agent.asr.cue_splitter import resolve_overlaps, split_cues


def test_empty_and_invalid_input():
    assert split_cues("", 0, 10) == []
    assert split_cues("  ", 0, 10) == []
    assert split_cues("あ", 5, 5) == []  # end <= start


def test_splits_at_sentence_punctuation():
    # Two sentences that together exceed max_chars -> two cues.
    cues = split_cues("今日はいい天気ですね。明日も晴れるでしょう。", 0, 10, max_chars=15)
    assert [c["text"] for c in cues] == ["今日はいい天気ですね。", "明日も晴れるでしょう。"]


def test_short_sentences_merge_up_to_max_chars():
    cues = split_cues("はい。うん。そう。", 0, 3, max_chars=24)
    assert [c["text"] for c in cues] == ["はい。うん。そう。"]


def test_merge_respects_max_chars():
    # "はい。" (3) + "うん。" (3) fit; adding "そうだね。" (5) would exceed 8.
    cues = split_cues("はい。うん。そうだね。", 0, 3, max_chars=8)
    assert [c["text"] for c in cues] == ["はい。うん。", "そうだね。"]


def test_long_sentence_wraps_at_soft_break():
    cues = split_cues("これはとても長い文章です、どこかで切らないといけません。", 0, 10, max_chars=15)
    assert [c["text"] for c in cues] == ["これはとても長い文章です、", "どこかで切らないといけません。"]


def test_long_sentence_hard_cut_without_punctuation():
    cues = split_cues("あ" * 30, 0, 10, max_chars=12)
    assert [len(c["text"]) for c in cues] == [12, 12, 6]


def test_proportional_time_allocation_without_words():
    cues = split_cues("前半です。後半です。", 10.0, 20.0, max_chars=6)
    assert cues[0]["start"] == 10.0 and cues[0]["end"] == 15.0
    assert cues[1]["start"] == 15.0 and cues[1]["end"] == 20.0


def test_word_timestamps_drive_cue_times():
    text = "雨です。雪です。"
    # 7 chars; words cover the text exactly, with uneven timing.
    words = [
        ("雨", 0.0, 0.5),
        ("です", 0.5, 1.5),
        ("。", 1.5, 1.6),
        ("雪", 5.0, 5.5),
        ("です", 5.5, 6.5),
        ("。", 6.5, 6.6),
    ]
    cues = split_cues(text, 0.0, 6.6, words=words, max_chars=4)
    assert [c["text"] for c in cues] == ["雨です。", "雪です。"]
    assert cues[0]["start"] == 0.0 and cues[0]["end"] == 1.6
    assert cues[1]["start"] == 5.0 and cues[1]["end"] == 6.6


def test_word_timestamps_fall_back_when_coverage_poor():
    # Words cover far fewer chars than the text -> proportional fallback.
    cues = split_cues("前半です。後半です。", 10.0, 20.0, words=[("前", 10.0, 11.0)], max_chars=6)
    assert cues[1]["end"] == 20.0
    assert cues[0]["end"] == 15.0


def test_word_boundaries_ignore_punctuation_drift():
    # Real aligner output drops punctuation — the char-offset scaling this
    # replaces used to drift with every 。/？ and land cue boundaries inside
    # the NEXT sentence's first word (936#1 regression). Cue edges must sit
    # on real word edges, leaving the inter-sentence silence to neither cue.
    text = "あなたまだなんですか？早くお礼状書かないとお相手の方に失礼ですよ。"
    words = [
        ("あなた", 0.48, 1.44),
        ("まだ", 1.44, 1.84),
        ("な", 1.84, 1.92),
        ("ん", 1.92, 2.00),
        ("です", 2.00, 2.16),
        ("か", 2.16, 2.64),
        # <-- 0.56s of silence here: か ends 2.64, 早く starts 3.20
        ("早く", 3.20, 3.60),
        ("お", 3.60, 3.68),
        ("礼", 3.68, 3.84),
        ("状", 3.84, 4.08),
        ("書か", 4.08, 4.40),
        ("ない", 4.40, 4.64),
        ("と", 4.64, 5.04),
        ("お", 5.36, 5.44),
        ("相手", 5.44, 5.76),
        ("の", 5.76, 5.84),
        ("方", 5.84, 6.08),
        ("に", 6.08, 6.24),
        ("失礼", 6.24, 6.64),
        ("です", 6.64, 6.96),
        ("よ", 6.96, 7.12),
    ]
    cues = split_cues(text, 0.4, 7.2, words=words, max_chars=24)
    assert [c["text"] for c in cues] == ["あなたまだなんですか？", "早くお礼状書かないとお相手の方に失礼ですよ。"]
    assert cues[0]["start"] == 0.48 and cues[0]["end"] == 2.64
    assert cues[1]["start"] == 3.20 and cues[1]["end"] == 7.12


def test_word_times_outside_chunk_are_kept():
    # The aligner runs on a window wider than the ASR chunk (to recover
    # VAD-clipped onsets): word edges outside [start, end] must survive.
    cues = split_cues("雨です。", 10.0, 10.6, words=[("雨", 9.6, 10.0), ("です", 10.0, 10.9)], max_chars=24)
    assert cues[0]["start"] == 9.6 and cues[0]["end"] == 10.9


def test_max_sec_forces_split():
    # One sentence, no punctuation; proportional 20s over 20 chars, max 8s.
    cues = split_cues("あ" * 20, 0.0, 20.0, max_chars=24, max_sec=8.0)
    assert all(c["end"] - c["start"] <= 8.0 + 1e-6 for c in cues)
    assert "".join(c["text"] for c in cues) == "あ" * 20


def test_long_pause_is_a_hard_cue_boundary():
    # Two short sentences with a long silence between them: under the char
    # budget they would merge, but a pause >= pause_sec must split them.
    text = "行くよ。待って。"
    words = [
        ("行く", 0.0, 0.5),
        ("よ", 0.5, 0.8),
        ("待っ", 3.0, 3.4),
        ("て", 3.4, 3.8),
    ]
    cues = split_cues(text, 0.0, 3.8, words=words, max_chars=24)
    assert [c["text"] for c in cues] == ["行くよ。", "待って。"]
    assert cues[0]["end"] == 0.8 and cues[1]["start"] == 3.0


def test_pause_inside_sentence_splits_without_punctuation():
    # No sentence punctuation at all; the pause alone decides the break.
    text = "それはちょっと無理だと思う"
    words = [
        ("それは", 0.0, 0.6),
        ("ちょっと", 0.6, 1.2),
        ("無理", 2.5, 2.9),
        ("だと", 2.9, 3.2),
        ("思う", 3.2, 3.6),
    ]
    cues = split_cues(text, 0.0, 3.6, words=words, max_chars=24)
    assert [c["text"] for c in cues] == ["それはちょっと", "無理だと思う"]
    assert cues[0]["end"] == 1.2 and cues[1]["start"] == 2.5


def test_short_pause_does_not_split():
    text = "行くよ。待って。"
    words = [
        ("行く", 0.0, 0.5),
        ("よ", 0.5, 0.8),
        ("待っ", 1.0, 1.4),
        ("て", 1.4, 1.8),
    ]
    cues = split_cues(text, 0.0, 1.8, words=words, max_chars=24)
    assert [c["text"] for c in cues] == ["行くよ。待って。"]


def test_cue_times_monotonic_and_clamped():
    cues = split_cues("a。b。c。", -1.0, 100.0, max_chars=2)
    for prev, cur in zip(cues, cues[1:]):
        assert cur["start"] >= prev["start"]
    assert all(c["end"] > c["start"] for c in cues)
    assert all(0.0 <= c["start"] <= 100.0 for c in cues)


# ---------------------------------------------------------------------------
# resolve_overlaps (cross-chunk de-overlap)
# ---------------------------------------------------------------------------


def test_resolve_overlaps_splits_at_midpoint():
    # Word-timed cue edges can spill past their chunk into the next one.
    segs = [
        {"start": 0.0, "end": 9.0, "text": "a"},
        {"start": 8.0, "end": 12.0, "text": "b"},
    ]
    out = resolve_overlaps(segs)
    assert out[0]["end"] == 8.5 and out[1]["start"] == 8.5
    assert out[0]["start"] == 0.0 and out[1]["end"] == 12.0


def test_resolve_overlaps_untouched_when_clean():
    segs = [
        {"start": 0.0, "end": 1.0, "text": "a"},
        {"start": 2.0, "end": 3.0, "text": "b"},
    ]
    out = resolve_overlaps(segs)
    assert [(s["start"], s["end"]) for s in out] == [(0.0, 1.0), (2.0, 3.0)]


def test_resolve_overlaps_sorts_and_handles_chains():
    segs = [
        {"start": 5.0, "end": 6.0, "text": "c"},
        {"start": 0.0, "end": 4.5, "text": "a"},
        {"start": 4.0, "end": 5.5, "text": "b"},
    ]
    out = resolve_overlaps([dict(s) for s in segs])
    assert [s["text"] for s in out] == ["a", "b", "c"]
    for prev, cur in zip(out, out[1:]):
        assert cur["start"] >= prev["end"] - 1e-9
        assert cur["end"] > cur["start"]


def test_resolve_overlaps_contained_cue_shares_at_boundary():
    # The later cue sits fully inside the earlier one; the overlap is still
    # split so both keep at least min_dur.
    segs = [
        {"start": 0.0, "end": 1.06, "text": "a"},
        {"start": 1.0, "end": 1.05, "text": "b"},
    ]
    out = resolve_overlaps(segs)
    assert out[0]["end"] == out[1]["start"]
    assert out[1]["end"] > out[1]["start"]


def test_resolve_overlaps_too_short_to_share():
    # Both cues together are shorter than 2*min_dur — the later cue is
    # pushed to start where the previous one ends.
    segs = [
        {"start": 0.0, "end": 0.09, "text": "a"},
        {"start": 0.05, "end": 0.1, "text": "b"},
    ]
    out = resolve_overlaps(segs)
    assert out[0]["end"] == 0.09
    assert out[1]["start"] == 0.09 and out[1]["end"] > out[1]["start"]


def test_resolve_overlaps_keeps_speaker_field():
    segs = [
        {"start": 0.0, "end": 2.0, "text": "a", "speaker": "話者1"},
        {"start": 1.5, "end": 3.0, "text": "b", "speaker": "話者2"},
    ]
    out = resolve_overlaps(segs)
    assert out[0]["speaker"] == "話者1" and out[1]["speaker"] == "話者2"
    assert out[0]["end"] == out[1]["start"]


# ---------------------------------------------------------------------------
# Speech-aware proportional fallback (no word timestamps)
# ---------------------------------------------------------------------------


def test_speech_fallback_hugs_utterance_edges():
    # Chunk spans 10-20s but speech only lives in two intervals; cues must
    # start/end on utterance edges, never cover the gap.
    speech = [(11.0, 13.0), (17.0, 19.0)]
    cues = split_cues("前半です。後半です。", 10.0, 20.0, max_chars=6, speech=speech)
    assert [c["text"] for c in cues] == ["前半です。", "後半です。"]
    assert cues[0]["start"] == 11.0 and cues[0]["end"] == 13.0
    assert cues[1]["start"] == 17.0 and cues[1]["end"] == 19.0


def test_speech_fallback_gap_is_hard_boundary():
    # One long sentence whose span contains a >pause_sec gap: the cue is cut
    # at the gap even though there is no punctuation there.
    speech = [(0.0, 2.0), (5.0, 7.0)]  # 3s gap
    cues = split_cues("ああああいいいい", 0.0, 7.0, max_chars=24, pause_sec=0.6, speech=speech)
    assert len(cues) == 2
    assert cues[0]["end"] <= 2.0 <= cues[1]["start"] - 2.9  # gap left subtitle-free


def test_speech_fallback_no_overlap_falls_back_to_linear():
    cues = split_cues("前半です。後半です。", 10.0, 20.0, max_chars=6, speech=[(30.0, 31.0)])
    assert cues[0]["start"] == 10.0 and cues[0]["end"] == 15.0  # plain linear


def test_speech_fallback_clips_intervals_to_chunk():
    # Speech spilling outside [start, end] is clipped to the chunk window.
    speech = [(9.0, 12.0), (18.0, 21.0)]
    cues = split_cues("前半です。後半です。", 10.0, 20.0, max_chars=6, speech=speech)
    assert cues[0]["start"] >= 10.0 and cues[-1]["end"] <= 20.0


def test_words_take_priority_over_speech():
    text = "雨です。雪です。"
    words = [
        ("雨", 0.0, 0.5),
        ("です", 0.5, 1.5),
        ("。", 1.5, 1.6),
        ("雪", 5.0, 5.5),
        ("です", 5.5, 6.5),
        ("。", 6.5, 6.6),
    ]
    speech = [(0.0, 3.0), (4.0, 6.6)]  # would give different edges
    cues = split_cues(text, 0.0, 6.6, words=words, max_chars=4, speech=speech)
    assert cues[0]["end"] == 1.6 and cues[1]["start"] == 5.0  # word edges win


# ---------------------------------------------------------------------------
# Hallucination repeat-tail cutting
# ---------------------------------------------------------------------------


def test_cut_repeat_tail_truncates_loop():
    from creator_agent.asr.cue_splitter import cut_repeat_tail

    assert cut_repeat_tail("よ。バイバイ。バイバイ。バイバイ。") == "よ。バイバイ。"
    assert cut_repeat_tail("白鳥、白鳥、白鳥、") == "白鳥、"


def test_cut_repeat_tail_keeps_real_speech():
    from creator_agent.asr.cue_splitter import cut_repeat_tail

    # Leading stutter + real content: not a tail, left alone.
    assert cut_repeat_tail("ねねねねねね何してるな？") == "ねねねねねね何してるな？"
    # Short repeats below the repeat count are kept.
    assert cut_repeat_tail("本当本当ですね") == "本当本当ですね"


def test_cut_repeat_tail_whole_text_loop():
    from creator_agent.asr.cue_splitter import cut_repeat_tail

    assert cut_repeat_tail("ああああああああ") == "あ"
