from fractions import Fraction

import pytest

from creator_agent.subtitle_extractor.timeline import FrameTimeline, VideoAnalysisError


def records_for(pts, *, durations=None, **kwargs):
    timeline = FrameTimeline(time_base=Fraction(1, 1000), **kwargs)
    records = []
    for index, value in enumerate(pts):
        record = timeline.push(index, pts=value, duration=durations[index] if durations is not None else None)
        if record is not None:
            records.append(record)
    last = timeline.finish()
    if last is not None:
        records.append(last)
    return timeline, records


def test_cfr_uses_pts_and_last_frame_display_duration():
    timeline, records = records_for([5000, 5040, 5080], durations=[40, 40, 40])
    assert timeline.frame_rate_mode == "CFR"
    assert [r.timing.pts_time for r in records] == [Fraction(5), Fraction(126, 25), Fraction(127, 25)]
    assert records[-1].end_time == Fraction(128, 25)
    assert timeline.anomalies == {}
    assert timeline.summary()["timestamp_complete_rate"] == 1


def test_vfr_is_not_computed_from_average_fps():
    timeline, records = records_for([0, 40, 100, 130], average_fps=Fraction(25))
    assert timeline.frame_rate_mode == "VFR"
    assert [r.duration for r in records] == [
        Fraction(40, 1000),
        Fraction(60, 1000),
        Fraction(30, 1000),
        Fraction(30, 1000),
    ]
    assert records[-1].duration_source == "previous_pts_interval"
    assert records[-1].end_time == Fraction(160, 1000)


def test_decoder_duration_takes_priority_and_mismatch_is_reported():
    timeline, records = records_for([0, 60, 120], durations=[40, 60, 60])
    assert records[0].duration == Fraction(40, 1000)
    assert records[0].duration_source == "decoder"
    assert timeline.anomalies["duration_pts_mismatch"] == 1


def test_clock_quantization_can_still_be_cfr():
    timeline, _ = records_for([0, 41, 83, 125, 166], average_fps=Fraction(24))
    assert timeline.frame_rate_mode == "CFR"


def test_coarse_time_base_does_not_hide_a_dropped_frame():
    timeline = FrameTimeline(time_base=Fraction(1, 25))
    for index, pts in enumerate([0, 1, 3]):
        timeline.push(index, pts=pts)
    timeline.finish()
    assert timeline.frame_rate_mode == "VFR"


def test_cfr_tolerance_is_configurable():
    timeline, _ = records_for([0, 40, 42], average_fps=Fraction(25), cfr_tolerance=Fraction(0))
    assert timeline.frame_rate_mode == "VFR"


def test_missing_pts_uses_previous_duration_without_rebasing():
    events = []
    timeline, records = records_for([2000, None, 2080], durations=[40, 40, 40], on_anomaly=events.append)
    assert records[1].timing.pts_time == Fraction(2040, 1000)
    assert records[1].timing.raw_pts is None
    assert records[1].timing.pts_source == "previous_frame_duration"
    assert timeline.frame_rate_mode == "unknown"
    assert timeline.summary()["timestamp_complete_rate"] == pytest.approx(2 / 3)
    assert events[0]["code"] == "missing_pts"


def test_missing_first_pts_uses_stream_start():
    timeline, records = records_for([None, 3040], durations=[40, 40], stream_start=Fraction(3))
    assert records[0].timing.pts_time == 3
    assert records[0].timing.pts_source == "stream_start"
    assert timeline.frame_rate_mode == "unknown"


def test_missing_timestamps_use_observed_interval_before_average_fps():
    _, records = records_for([0, 60, None], average_fps=Fraction(25))
    assert records[-1].timing.pts_time == Fraction(120, 1000)
    assert records[-1].timing.pts_source == "previous_pts_interval"


def test_nominal_fps_fallback_is_explicit_and_reported():
    timeline, records = records_for([None, None], average_fps=Fraction(25))
    assert records[0].timing.pts_source == "zero_origin"
    assert records[1].timing.pts_source == "average_fps"
    assert records[1].timing.pts_time == Fraction(1, 25)
    assert records[-1].duration_source == "average_fps"
    assert timeline.anomalies["missing_pts"] == 2
    assert timeline.frame_rate_mode == "unknown"


def test_user_fallback_overrides_nominal_metadata():
    _, records = records_for([0], average_fps=Fraction(25), fallback_fps=Fraction(20))
    assert records[0].duration == Fraction(1, 20)
    assert records[0].duration_source == "configured_fps"


def test_last_frame_can_use_video_stream_end():
    timeline, records = records_for([1000], stream_end=Fraction(1040, 1000))
    assert records[0].end_time == Fraction(1040, 1000)
    assert records[0].duration_source == "stream_end"
    assert timeline.frame_rate_mode == "unknown"


def test_duplicate_and_backwards_pts_are_kept_and_logged():
    timeline, records = records_for([0, 40, 40, 20], average_fps=Fraction(25))
    assert [r.timing.raw_pts for r in records] == [0, 40, 40, 20]
    assert timeline.anomalies["duplicate_pts"] == 1
    assert timeline.anomalies["non_monotonic_pts"] == 1
    assert timeline.frame_rate_mode == "unknown"
    assert all(r.duration > 0 for r in records)


def test_negative_pts_are_not_silently_zeroed():
    timeline, records = records_for([-40, 0, 40], durations=[40, 40, 40])
    assert records[0].timing.pts_time == Fraction(-40, 1000)
    assert timeline.anomalies["negative_pts"] == 1


def test_large_gap_warning_is_configurable():
    timeline, _ = records_for([0, 100, 2000], average_fps=Fraction(25), gap_warning_seconds=Fraction(1, 2))
    assert timeline.anomalies["large_pts_gap"] == 1


def test_exact_fractional_time_base_is_retained():
    timeline = FrameTimeline(time_base=Fraction(1, 24000))
    assert timeline.push(0, pts=0, duration=1001) is None
    first = timeline.push(1, pts=1001, duration=1001)
    assert first.duration == Fraction(1001, 24000)
    assert timeline.finish().end_time == Fraction(1001, 12000)
    assert timeline.frame_rate_mode == "unknown"


def test_frame_time_base_overrides_stream_time_base():
    timeline = FrameTimeline(time_base=Fraction(1, 1000))
    timeline.push(0, pts=90, time_base=Fraction(1, 90), duration=3)
    record = timeline.finish()
    assert record.timing.pts_time == 1
    assert record.duration == Fraction(1, 30)


def test_missing_duration_without_any_fallback_fails_clearly():
    timeline = FrameTimeline(time_base=Fraction(1, 1000))
    timeline.push(0, pts=0)
    with pytest.raises(VideoAnalysisError, match="fallback-fps"):
        timeline.finish()


def test_invalid_duration_is_logged_before_fallback():
    timeline, records = records_for([0], durations=[-2], average_fps=Fraction(25))
    assert records[0].duration == Fraction(1, 25)
    assert timeline.anomalies["invalid_duration"] == 1


def test_empty_timeline_and_finish_are_idempotent():
    timeline = FrameTimeline(time_base=Fraction(1, 1000))
    assert timeline.finish() is None
    assert timeline.finish() is None
    assert timeline.frame_rate_mode == "unknown"
    assert timeline.summary()["timestamp_complete_rate"] == 0


def test_frames_cannot_be_reordered_or_added_after_finish():
    timeline = FrameTimeline(time_base=Fraction(1, 1000))
    with pytest.raises(VideoAnalysisError, match="display order"):
        timeline.push(1, pts=0)
    timeline.push(0, pts=0, duration=40)
    timeline.finish()
    with pytest.raises(VideoAnalysisError, match="display order"):
        timeline.push(1, pts=40)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"time_base": Fraction(0)},
        {"time_base": Fraction(1), "fallback_fps": Fraction(0)},
        {"time_base": Fraction(1), "gap_warning_seconds": Fraction(0)},
        {"time_base": Fraction(1), "cfr_tolerance": Fraction(-1)},
    ],
)
def test_invalid_configuration(kwargs):
    with pytest.raises(VideoAnalysisError):
        FrameTimeline(**kwargs)


def test_cfr_classification_uses_actual_frame_clock_precision():
    timeline = FrameTimeline(time_base=Fraction(1, 1000))
    for index, pts in enumerate([0, 960, 1932]):
        timeline.push(index, pts=pts, time_base=Fraction(1, 24000), duration=960)
    timeline.finish()
    assert timeline.frame_rate_mode == "VFR"
