"""Build a display-order frame index without assuming constant frame rate."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any


class VideoAnalysisError(Exception):
    """A video could not be indexed reliably."""


@dataclass(frozen=True)
class FrameTiming:
    frame_index: int
    raw_pts: int | None
    time_base: Fraction
    pts_time: Fraction
    pts_source: str
    decoder_duration: Fraction | None


@dataclass(frozen=True)
class FrameRecord:
    timing: FrameTiming
    duration: Fraction
    duration_source: str

    @property
    def end_time(self) -> Fraction:
        return self.timing.pts_time + self.duration


class FrameTimeline:
    """Keep one pending frame so its duration can use the next display PTS.

    Original PTS are never rebased, sorted, or silently repaired. Every inferred
    value has a source in the index; anomalies also go to the supplied sink.
    """

    def __init__(
        self,
        *,
        time_base: Fraction,
        average_fps: Fraction | None = None,
        fallback_fps: Fraction | None = None,
        stream_start: Fraction | None = None,
        stream_end: Fraction | None = None,
        gap_warning_seconds: Fraction = Fraction(1),
        cfr_tolerance: Fraction | None = None,
        on_anomaly: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if time_base <= 0:
            raise VideoAnalysisError("Video time_base must be positive")
        if average_fps is not None and average_fps <= 0:
            average_fps = None
        if fallback_fps is not None and fallback_fps <= 0:
            raise VideoAnalysisError("fallback_fps must be positive")
        if gap_warning_seconds <= 0:
            raise VideoAnalysisError("gap_warning_seconds must be positive")
        if cfr_tolerance is not None and cfr_tolerance < 0:
            raise VideoAnalysisError("cfr_tolerance cannot be negative")
        self.time_base = time_base
        self._clock_precision = time_base
        self.average_fps = average_fps
        self.fallback_fps = fallback_fps
        self.stream_start = stream_start
        self.stream_end = stream_end
        self.gap_warning_seconds = gap_warning_seconds
        self.cfr_tolerance = cfr_tolerance
        self.on_anomaly = on_anomaly
        self.anomalies: Counter[str] = Counter()
        self.pts_sources: Counter[str] = Counter()
        self.duration_sources: Counter[str] = Counter()
        self.frame_count = 0
        self.first_pts: Fraction | None = None
        self.last_end: Fraction | None = None
        self._pending: FrameTiming | None = None
        self._last_delta: Fraction | None = None
        self._min_delta: Fraction | None = None
        self._max_delta: Fraction | None = None
        self._finished = False

    def report(self, code: str, frame_index: int, **details: Any) -> None:
        self.anomalies[code] += 1
        if self.on_anomaly is not None:
            self.on_anomaly({"code": code, "frame_index": frame_index, **details})

    def _nominal_duration(self) -> tuple[Fraction, str]:
        if self.fallback_fps is not None:
            return 1 / self.fallback_fps, "configured_fps"
        if self.average_fps is not None:
            return 1 / self.average_fps, "average_fps"
        raise VideoAnalysisError(
            "Cannot infer missing frame timing: no usable PTS interval, frame "
            "duration, or frame rate. Supply --fallback-fps explicitly."
        )

    def _tolerance(self, interval: Fraction) -> Fraction:
        if self.cfr_tolerance is not None:
            return self.cfr_tolerance
        # Allow clock quantization, but do not classify a dropped frame as CFR
        # just because a coarse clock tick equals one whole frame interval.
        return min(self._clock_precision, interval / 20) + Fraction(1, 1_000_000_000)

    def push(
        self,
        frame_index: int,
        *,
        pts: int | None,
        time_base: Fraction | None = None,
        duration: int | None = None,
    ) -> FrameRecord | None:
        if self._finished or frame_index != self.frame_count:
            raise VideoAnalysisError("Frames must be indexed once, in display order")
        clock = time_base if time_base is not None else self.time_base
        if clock <= 0:
            raise VideoAnalysisError(f"Invalid time_base at frame {frame_index}")
        self._clock_precision = min(self._clock_precision, clock)
        decoder_duration = duration * clock if duration is not None and duration > 0 else None
        if duration is not None and duration < 0:
            self.report("invalid_duration", frame_index, raw_duration=duration)
        if pts is not None:
            pts_time, pts_source = pts * clock, "decoder"
        elif self._pending is None:
            pts_time = self.stream_start if self.stream_start is not None else Fraction(0)
            pts_source = "stream_start" if self.stream_start is not None else "zero_origin"
        else:
            if self._pending.decoder_duration is not None:
                step, step_source = self._pending.decoder_duration, "previous_frame_duration"
            elif self._last_delta is not None:
                step, step_source = self._last_delta, "previous_pts_interval"
            else:
                step, step_source = self._nominal_duration()
            pts_time, pts_source = self._pending.pts_time + step, step_source
        if pts is None:
            self.report("missing_pts", frame_index, inferred_pts=float(pts_time), source=pts_source)
        if pts_time < 0:
            self.report("negative_pts", frame_index, pts_time=float(pts_time))

        current = FrameTiming(frame_index, pts, clock, pts_time, pts_source, decoder_duration)
        record = None
        if self._pending is not None:
            delta = current.pts_time - self._pending.pts_time
            if delta <= 0:
                self.report(
                    "duplicate_pts" if delta == 0 else "non_monotonic_pts",
                    frame_index,
                    previous_pts=float(self._pending.pts_time),
                    pts_time=float(pts_time),
                )
            else:
                if delta > self.gap_warning_seconds:
                    self.report("large_pts_gap", frame_index, interval=float(delta))
                if current.pts_source == self._pending.pts_source == "decoder":
                    self._last_delta = delta
                    self._min_delta = min(self._min_delta, delta) if self._min_delta else delta
                    self._max_delta = max(self._max_delta, delta) if self._max_delta else delta
            record = self._finalize(self._pending, current)
        self._pending = current
        self.frame_count += 1
        self.pts_sources[pts_source] += 1
        if self.first_pts is None:
            self.first_pts = pts_time
        return record

    def _finalize(self, frame: FrameTiming, following: FrameTiming | None) -> FrameRecord:
        delta = following.pts_time - frame.pts_time if following is not None else None
        if frame.decoder_duration is not None:
            value, source = frame.decoder_duration, "decoder"
            if delta is not None and delta > 0 and abs(value - delta) > self._tolerance(delta):
                self.report(
                    "duration_pts_mismatch",
                    frame.frame_index,
                    decoder_duration=float(value),
                    next_pts_interval=float(delta),
                )
        elif delta is not None and delta > 0:
            value = delta
            source = "next_pts" if following.pts_source == "decoder" else "next_inferred_pts"
        elif following is None and self.stream_end is not None and self.stream_end > frame.pts_time:
            value, source = self.stream_end - frame.pts_time, "stream_end"
        elif self._last_delta is not None:
            value, source = self._last_delta, "previous_pts_interval"
        else:
            value, source = self._nominal_duration()
        if source not in {"decoder", "next_pts"}:
            self.report("inferred_duration", frame.frame_index, duration=float(value), source=source)
        self.duration_sources[source] += 1
        record = FrameRecord(frame, value, source)
        self.last_end = record.end_time
        return record

    def finish(self) -> FrameRecord | None:
        if self._finished:
            return None
        self._finished = True
        if self._pending is None:
            return None
        record = self._finalize(self._pending, None)
        self._pending = None
        return record

    @property
    def frame_rate_mode(self) -> str:
        if (
            self.frame_count < 3
            or self._min_delta is None
            or self._max_delta is None
            or any(self.anomalies[code] for code in ("missing_pts", "duplicate_pts", "non_monotonic_pts"))
        ):
            return "unknown"
        return "CFR" if self._max_delta - self._min_delta <= self._tolerance(self._min_delta) else "VFR"

    def summary(self) -> dict[str, Any]:
        return {
            "frame_rate_mode": self.frame_rate_mode,
            "first_pts": float(self.first_pts) if self.first_pts is not None else None,
            "last_display_end": float(self.last_end) if self.last_end is not None else None,
            "min_pts_interval": float(self._min_delta) if self._min_delta is not None else None,
            "max_pts_interval": float(self._max_delta) if self._max_delta is not None else None,
            "timestamp_complete_rate": (self.pts_sources["decoder"] / self.frame_count if self.frame_count else 0.0),
            "pts_sources": dict(self.pts_sources),
            "duration_sources": dict(self.duration_sources),
            "anomaly_counts": dict(self.anomalies),
        }
