from __future__ import annotations

from creator_agent.asr.cue_splitter import split_cues


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


def test_cue_times_monotonic_and_clamped():
    cues = split_cues("a。b。c。", -1.0, 100.0, max_chars=2)
    for prev, cur in zip(cues, cues[1:]):
        assert cur["start"] >= prev["start"]
    assert all(c["end"] > c["start"] for c in cues)
    assert all(0.0 <= c["start"] <= 100.0 for c in cues)
