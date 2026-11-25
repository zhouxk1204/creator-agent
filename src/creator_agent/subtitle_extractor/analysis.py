"""Stage 0: stream video frames and persist a traceable PTS index."""

import csv
import importlib
import json
import logging
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from creator_agent.subtitle_extractor.timeline import FrameRecord, FrameTimeline, VideoAnalysisError


@dataclass(frozen=True)
class AnalysisResult:
    output_dir: Path
    video_info: dict[str, Any]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_av() -> Any:
    try:
        return importlib.import_module("av")
    except ImportError as exc:
        raise VideoAnalysisError("PyAV is not installed. Run: uv sync --extra subtitle-extractor") from exc


def analyze_video(
    input_path: Path,
    *,
    output_dir: Path | None = None,
    save_frames: bool = True,
    fallback_fps: float | None = None,
    gap_warning_seconds: float = 1.0,
    cfr_tolerance: float | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> AnalysisResult:
    input_path = input_path.expanduser().resolve()
    if not input_path.is_file():
        raise VideoAnalysisError(f"Input video does not exist or is not a file: {input_path}")
    output_dir = (
        output_dir.expanduser().resolve() if output_dir is not None else (Path("output") / input_path.stem).resolve()
    )
    try:
        if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
            raise VideoAnalysisError(
                f"Output path is not an empty directory: {output_dir}. "
                "Existing results are preserved; choose a different --output directory."
            )
        av = _load_av()
        with av.open(str(input_path)) as container:
            if not container.streams.video:
                raise VideoAnalysisError("Input has no video stream")
            stream = container.streams.video[0]
            if stream.time_base is None:
                raise VideoAnalysisError("Video stream has no time_base")
            time_base = Fraction(stream.time_base)
            average_fps = Fraction(stream.average_rate) if stream.average_rate else None
            stream_start = stream.start_time * time_base if stream.start_time is not None else None
            stream_duration = (
                stream.duration * time_base if stream.duration is not None and stream.duration > 0 else None
            )
            stream_end = (
                stream_start + stream_duration if stream_duration is not None and stream_start is not None else None
            )
            timeline = FrameTimeline(
                time_base=time_base,
                average_fps=average_fps,
                fallback_fps=Fraction(str(fallback_fps)) if fallback_fps is not None else None,
                stream_start=stream_start,
                stream_end=stream_end,
                gap_warning_seconds=Fraction(str(gap_warning_seconds)),
                cfr_tolerance=Fraction(str(cfr_tolerance)) if cfr_tolerance is not None else None,
            )
            expected_frames = stream.frames if stream.frames > 0 else None
            duration = (
                float(stream_duration)
                if stream_duration is not None
                else container.duration / av.time_base
                if container.duration is not None
                else None
            )
            info = {
                "video_path": str(input_path),
                "duration": duration,
                "duration_source": (
                    "video_stream"
                    if stream_duration is not None
                    else "container"
                    if duration is not None
                    else "decoded_display_span"
                ),
                "width": stream.codec_context.width,
                "height": stream.codec_context.height,
                "codec": stream.codec_context.name,
                "video_stream_index": stream.index,
                "average_fps": float(average_fps) if average_fps is not None else None,
                "expected_frame_count": expected_frames,
                "frame_count": 0,
                "frame_rate_mode": "unknown",
                "time_base": str(time_base),
                "stream_start_time": float(stream_start) if stream_start is not None else None,
                "save_frames": save_frames,
            }
            return _decode(
                container,
                stream,
                timeline,
                input_path,
                output_dir,
                info,
                save_frames=save_frames,
                on_progress=on_progress,
            )
    except VideoAnalysisError:
        raise
    except Exception as exc:
        raise VideoAnalysisError(f"Cannot analyze video {input_path}: {exc}") from exc


def _decode(
    container: Any,
    stream: Any,
    timeline: FrameTimeline,
    input_path: Path,
    output_dir: Path,
    info: dict[str, Any],
    *,
    save_frames: bool,
    on_progress: Callable[[int, int | None], None] | None,
) -> AnalysisResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    stat = input_path.stat()
    task = {
        "stage": "stage0",
        "status": "running",
        "input_path": str(input_path),
        "input_size": stat.st_size,
        "input_mtime_ns": stat.st_mtime_ns,
        "options": {
            "save_frames": save_frames,
            "fallback_fps": float(timeline.fallback_fps) if timeline.fallback_fps else None,
            "gap_warning_seconds": float(timeline.gap_warning_seconds),
            "cfr_tolerance": (float(timeline.cfr_tolerance) if timeline.cfr_tolerance is not None else None),
        },
    }
    try:
        with (output_dir / "task_status.json").open("x", encoding="utf-8") as task_file:
            task_file.write(json.dumps(task, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as exc:
        raise VideoAnalysisError(f"Another task already owns the output directory: {output_dir}") from exc
    logger = logging.Logger("subtitle_extractor.stage0")
    with ExitStack() as stack:
        handler = logging.FileHandler(output_dir / "analysis.log", encoding="utf-8")
        stack.callback(handler.close)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        anomalies_file = stack.enter_context(
            (output_dir / "timestamp_anomalies.jsonl").open("w", encoding="utf-8", buffering=1)
        )
        index_file = stack.enter_context((output_dir / "frame_index.csv").open("w", encoding="utf-8", newline=""))
        writer = csv.writer(index_file)
        writer.writerow(
            [
                "frame_index",
                "image_path",
                "pts_time",
                "duration",
                "raw_pts",
                "time_base",
                "pts_source",
                "duration_source",
                "pts_numerator",
                "pts_denominator",
                "duration_numerator",
                "duration_denominator",
            ]
        )
        decoded_frames = 0
        indexed_frames = 0

        def anomaly(event: dict[str, Any]) -> None:
            line = json.dumps(event, ensure_ascii=False)
            anomalies_file.write(line + "\n")
            logger.warning(line)

        timeline.on_anomaly = anomaly

        def write_record(record: FrameRecord) -> None:
            nonlocal indexed_frames
            timing = record.timing
            writer.writerow(
                [
                    timing.frame_index,
                    f"frames/{timing.frame_index:06d}.png" if save_frames else "",
                    f"{float(timing.pts_time):.9f}",
                    f"{float(record.duration):.9f}",
                    timing.raw_pts if timing.raw_pts is not None else "",
                    str(timing.time_base),
                    timing.pts_source,
                    record.duration_source,
                    timing.pts_time.numerator,
                    timing.pts_time.denominator,
                    record.duration.numerator,
                    record.duration.denominator,
                ]
            )
            indexed_frames += 1

        def persist(status: str, error: str | None = None) -> None:
            summary = timeline.summary()
            info.update(summary)
            info.update(
                {
                    "status": status,
                    "frame_count": decoded_frames,
                    "index_record_count": indexed_frames,
                    "decoded_duration": (
                        float(timeline.last_end - timeline.first_pts)
                        if timeline.last_end is not None and timeline.first_pts is not None
                        else None
                    ),
                }
            )
            if info["duration"] is None:
                info["duration"] = info["decoded_duration"]
            task.update(
                {
                    "status": status,
                    "decoded_frames": decoded_frames,
                    "index_records": indexed_frames,
                    "anomaly_counts": summary["anomaly_counts"],
                }
            )
            if error is not None:
                task["error"] = error
            index_file.flush()
            _write_json(output_dir / "video_info.json", info)
            _write_json(output_dir / "task_status.json", task)

        logger.info("Stage 0 started: %s", input_path)
        try:
            if save_frames:
                (output_dir / "frames").mkdir()
            for frame_index, frame in enumerate(container.decode(stream)):
                decoded_frames += 1
                if frame.width != info["width"] or frame.height != info["height"]:
                    timeline.report("frame_size_changed", frame_index, width=frame.width, height=frame.height)
                if getattr(frame, "is_corrupt", False):
                    timeline.report("corrupt_frame", frame_index)
                if frame.time_base is None:
                    timeline.report("frame_time_base_from_stream", frame_index, time_base=str(timeline.time_base))
                record = timeline.push(
                    frame_index,
                    pts=frame.pts,
                    time_base=Fraction(frame.time_base) if frame.time_base is not None else None,
                    duration=getattr(frame, "duration", None),
                )
                if save_frames:
                    frame.to_image().save(output_dir / "frames" / f"{frame_index:06d}.png")
                if record is not None:
                    write_record(record)
                if on_progress is not None:
                    on_progress(decoded_frames, info["expected_frame_count"])
            if decoded_frames == 0:
                raise VideoAnalysisError("Video stream did not yield any decoded frames")
            last_record = timeline.finish()
            if last_record is not None:
                write_record(last_record)
            if info["expected_frame_count"] is not None and decoded_frames != info["expected_frame_count"]:
                timeline.report(
                    "frame_count_mismatch",
                    decoded_frames - 1,
                    expected=info["expected_frame_count"],
                    decoded=decoded_frames,
                )
            if indexed_frames != decoded_frames:
                raise VideoAnalysisError("Decoded frame count does not match index record count")
            persist("completed")
            logger.info(
                "Stage 0 completed: %d frames, %s, %d anomalies",
                decoded_frames,
                timeline.frame_rate_mode,
                sum(timeline.anomalies.values()),
            )
        except (Exception, KeyboardInterrupt) as exc:
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            logger.exception("Stage 0 %s; completed data is preserved", status)
            persist(status, str(exc) or type(exc).__name__)
            raise
    return AnalysisResult(output_dir=output_dir, video_info=info)
