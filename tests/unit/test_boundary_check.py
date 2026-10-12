"""Unit tests for asr/boundary_check.py — refine_cues is pure; the
silencedetect parser is tested on fixture text (no ffmpeg process)."""

from __future__ import annotations

from creator_agent.asr.boundary_check import (
    AUDIO_VIDEO_MISMATCH,
    END_IN_SILENCE,
    EXCEEDS_VIDEO,
    NO_SPEECH,
    START_IN_SILENCE,
    parse_silencedetect,
    refine_cues,
)

SILENCE_FIXTURE = """
[Parsed_silencedetect_0 @ 000001] silence_start: 0
[Parsed_silencedetect_0 @ 000001] silence_end: 1.240 | silence_duration: 1.240
[Parsed_silencedetect_0 @ 000001] silence_start: 5.500
[Parsed_silencedetect_0 @ 000001] silence_end: 6.000 | silence_duration: 0.500
[Parsed_silencedetect_0 @ 000001] silence_start: 9.750
"""


def test_parse_silencedetect_basic():
    silences = parse_silencedetect(SILENCE_FIXTURE)
    assert silences == [(0.0, 1.240), (5.5, 6.0)]


def test_parse_silencedetect_closes_trailing_with_duration():
    silences = parse_silencedetect(SILENCE_FIXTURE, duration=12.0)
    assert silences == [(0.0, 1.240), (5.5, 6.0), (9.75, 12.0)]


def test_parse_silencedetect_empty():
    assert parse_silencedetect("no silence here") == []


def _cue(start, end, text="テスト"):
    return {"start": start, "end": end, "text": text}


def _reasons(flags, idx):
    return {r for f in flags if f["index"] == idx for r in f["reasons"]}


def test_snap_start_inside_silence_moves_to_onset():
    # cue starts mid-silence -> start snaps to silence_end - lead_in.
    cues, flags = refine_cues([_cue(5.7, 8.0)], [(5.5, 6.0)], audio_duration=10.0, lead_in=0.1)
    assert cues[0]["start"] == 5.9  # 6.0 - 0.1
    assert not any(f["index"] == 0 for f in flags)


def test_snap_end_inside_silence_pulls_back_to_offset():
    # cue ends mid-silence -> end snaps to silence_start + tail_out.
    cues, flags = refine_cues([_cue(1.3, 5.8)], [(5.5, 6.0)], audio_duration=10.0, tail_out=0.1)
    assert cues[0]["end"] == 5.6  # 5.5 + 0.1
    assert not any(f["index"] == 0 for f in flags)


def test_mid_speech_start_polished_to_nearby_onset():
    # cue starts just after the real onset (mid-speech) -> snaps back to
    # silence_end - lead_in; mid-speech edges are never flagged.
    cues, flags = refine_cues([_cue(1.35, 5.2)], [(0.0, 1.24)], audio_duration=10.0, lead_in=0.1)
    assert cues[0]["start"] == 1.14  # 1.24 - 0.1
    assert not any(f["index"] == 0 for f in flags)


def test_mid_speech_end_extended_to_nearby_offset():
    cues, flags = refine_cues([_cue(1.3, 5.3)], [(5.5, 6.0)], audio_duration=10.0, tail_out=0.1)
    assert cues[0]["end"] == 5.6  # 5.5 + 0.1
    # a successful snap within the window is a trusted correction — no flag
    assert not any(f["index"] == 0 for f in flags)


def test_mid_speech_edge_without_nearby_silence_untouched_and_unflagged():
    # No silence within the snap window: normal for cues split at punctuation
    # inside one utterance. Time kept, no flag.
    cues, flags = refine_cues([_cue(2.0, 4.0)], [(0.0, 0.5), (8.0, 9.0)], audio_duration=10.0, snap_window=0.5)
    assert cues[0]["start"] == 2.0 and cues[0]["end"] == 4.0
    assert not any(f["index"] == 0 for f in flags)


def test_start_deep_in_silence_kept_and_flagged():
    # start sits 0.8s into a silence; snapping to the onset would move +0.9s
    # (> snap_window) -> keep original, flag START_IN_SILENCE.
    cues, flags = refine_cues([_cue(6.3, 8.0)], [(5.5, 7.3)], audio_duration=10.0, snap_window=0.5, lead_in=0.1)
    assert cues[0]["start"] == 6.3
    assert START_IN_SILENCE in _reasons(flags, 0)


def test_end_deep_in_silence_kept_and_flagged():
    # end sits 0.8s into a silence; pulling back would move -0.7s -> flag.
    cues, flags = refine_cues([_cue(4.0, 6.3)], [(5.5, 7.3)], audio_duration=10.0, snap_window=0.5, tail_out=0.1)
    assert cues[0]["end"] == 6.3
    assert END_IN_SILENCE in _reasons(flags, 0)


def test_large_snap_within_window_trusted_not_flagged():
    # start sits mid-silence, 0.5s before its end; window 0.8 -> snaps to the
    # onset. A snap that succeeds within the window is trusted — no flag.
    cues, flags = refine_cues([_cue(5.7, 8.0)], [(5.5, 6.3)], audio_duration=10.0, snap_window=0.8, lead_in=0.1)
    assert cues[0]["start"] == 6.2
    assert not any(f["index"] == 0 for f in flags)


def test_no_speech_flagged():
    # cue entirely inside a long silence -> NO_SPEECH.
    cues, flags = refine_cues([_cue(6.0, 7.0)], [(5.0, 8.0)], audio_duration=10.0, snap_window=0.05)
    assert NO_SPEECH in _reasons(flags, 0)


def test_exceeds_video_clamped_and_flagged():
    cues, flags = refine_cues([_cue(8.0, 10.5)], [], audio_duration=11.0, video_duration=10.0, tail_out=0.0)
    assert cues[0]["end"] <= 10.0
    assert EXCEEDS_VIDEO in _reasons(flags, 0)


def test_audio_video_mismatch_video_level_flag():
    _, flags = refine_cues([_cue(1.0, 2.0)], [], audio_duration=90.0, video_duration=100.0)
    video_flags = [f for f in flags if f["index"] is None]
    assert video_flags and AUDIO_VIDEO_MISMATCH in video_flags[0]["reasons"]


def test_no_overlap_after_snap():
    # Two cues snap into the same gap; they must not overlap afterwards.
    silences = [(5.0, 6.0)]
    cues = [_cue(4.9, 5.5, "a"), _cue(5.6, 8.0, "b")]
    refined, _ = refine_cues(cues, silences, audio_duration=10.0, lead_in=0.1, tail_out=0.1)
    assert refined[0]["end"] <= refined[1]["start"]
    for c in refined:
        assert c["end"] > c["start"]


def test_extra_keys_preserved():
    cues, _ = refine_cues(
        [{"start": 1.3, "end": 5.2, "text": "x", "speaker": "話者A"}],
        [(0.0, 1.24)],
        audio_duration=10.0,
    )
    assert cues[0]["speaker"] == "話者A"


def test_format_check_report_uncovered():
    from creator_agent.asr.boundary_check import format_check_report

    report = format_check_report("ep1", 100.0, 100.0, 10, 3, [], [], uncovered=[[12.0, 14.5]])
    assert "UNRECOVERED_SPEECH" in report
    assert "00:12.000 --> 00:14.500" in report
    # No flags AND no uncovered -> clean message (unchanged old behavior).
    report = format_check_report("ep1", 100.0, 100.0, 10, 3, [], [])
    assert "无可疑项" in report and "UNRECOVERED" not in report
