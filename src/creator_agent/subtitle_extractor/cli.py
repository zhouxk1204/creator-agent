"""Independent CLI for the staged anime hard-subtitle extractor."""

import json
import math
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from creator_agent.subtitle_extractor.analysis import analyze_video
from creator_agent.subtitle_extractor.crop import (
    MAX_SCALE,
    MIN_SCALE,
    CropConfig,
    CropError,
    parse_region,
    parse_variants,
)
from creator_agent.subtitle_extractor.detection import DetectionConfig, DetectionError
from creator_agent.subtitle_extractor.extraction import ExtractionConfig, ExtractionError
from creator_agent.subtitle_extractor.grouping import GroupingConfig, GroupingError
from creator_agent.subtitle_extractor.ocr import OcrError, create_ocr_engine
from creator_agent.subtitle_extractor.reprocess import analyze_crops
from creator_agent.subtitle_extractor.timeline import VideoAnalysisError

app = typer.Typer(help="Anime hard-subtitle extractor — stage 0 frame index plus stage 1 subtitle crops.")


def echo(message: str) -> None:
    """Timestamped console line so redirected logs stay readable."""
    typer.echo(f"[{time.strftime('%H:%M:%S')}] {message}")


class ProgressLine:
    """Single updating line: ``817 / 18718  0:04:24  45%``.

    Redraws in place on a terminal and prints one line every few seconds when
    output is redirected, so a log file stays greppable.
    """

    def __init__(self, unit: str = "frames", interval: float = 4.0) -> None:
        self.unit = unit
        self.interval = interval
        self.tty = sys.stdout.isatty()
        self.started = time.monotonic()
        self.last_draw = 0.0
        self.total: int | None = None
        self.text = ""

    def _render(self, completed: int) -> str:
        total = str(self.total) if self.total else "?"
        elapsed = time.monotonic() - self.started
        minutes, seconds = divmod(int(elapsed), 60)
        percent = f"{completed / self.total * 100:3.0f}%" if self.total else "  ?%"
        return f"{completed} / {total}  {minutes}:{seconds:02d}  {percent}"

    def update(self, completed: int, expected: int | None = None) -> None:
        if expected:
            self.total = expected
        line = self._render(completed)
        now = time.monotonic()
        if self.tty:
            sys.stdout.write(f"\r{line}")
            sys.stdout.flush()
            self.last_draw = now
            self.text = line
        elif now - self.last_draw >= self.interval or completed == self.total:
            self.last_draw = now
            print(line, flush=True)

    def done(self) -> None:
        line = self._render(self.total or 0)
        if self.tty and self.text:
            sys.stdout.write(f"\r{line}\n")
            sys.stdout.flush()
        elif not self.tty:
            print(line, flush=True)


@app.command()
def main(
    input_path: Annotated[
        Path | None,
        typer.Option(
            "--input",
            "-i",
            exists=True,
            file_okay=True,
            dir_okay=False,
            help="Input video file (omit with --from-crops).",
        ),
    ] = None,
    from_crops: Annotated[
        Path | None,
        typer.Option(
            "--from-crops",
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="Existing stage-0 task folder (with crops/ and frame_index.csv): skip decoding.",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Empty task directory (default: <doraemon materials>/<video stem>_srt, else output/<stem>_srt).",
        ),
    ] = None,
    save_frames: Annotated[
        bool,
        typer.Option("--save-frames/--no-save-frames", help="Save every decoded frame as PNG (may use a lot of disk)."),
    ] = True,
    fallback_fps: Annotated[
        float | None, typer.Option("--fallback-fps", help="Explicit FPS fallback only when timing data is missing.")
    ] = None,
    gap_warning_seconds: Annotated[
        float,
        typer.Option("--gap-warning-seconds", help="Log PTS intervals larger than this (not proof of frame loss)."),
    ] = 1.0,
    cfr_tolerance: Annotated[
        float | None,
        typer.Option("--cfr-tolerance", help="CFR interval tolerance in seconds; default uses clock precision."),
    ] = None,
    crop: Annotated[
        bool,
        typer.Option("--crop/--no-crop", help="Crop the subtitle region and write stage-1 outputs (crops/)."),
    ] = True,
    crop_unit: Annotated[
        str,
        typer.Option("--crop-unit", help="Coordinate unit for --crop-box: percent (0-100) or pixel."),
    ] = "percent",
    crop_box: Annotated[
        str,
        typer.Option("--crop-box", help="Subtitle region as x_start,y_start,x_end,y_end in --crop-unit."),
    ] = "5,72,95,97",
    crop_scale: Annotated[
        float,
        typer.Option("--crop-scale", help=f"Resize factor for the crop, {MIN_SCALE}-{MAX_SCALE} (1.0 keeps pixels)."),
    ] = 1.0,
    crop_variants: Annotated[
        str,
        typer.Option("--crop-variants", help="Comma list of gray,clahe,otsu,adaptive; use none for color crops only."),
    ] = "gray,clahe,otsu,adaptive",
    clahe_clip_limit: Annotated[
        float, typer.Option("--clahe-clip-limit", help="CLAHE clip limit used by the clahe variant.")
    ] = 2.0,
    adaptive_block_size: Annotated[
        int, typer.Option("--adaptive-block-size", help="Odd neighborhood size for the adaptive variant.")
    ] = 31,
    adaptive_c: Annotated[
        float, typer.Option("--adaptive-c", help="Constant subtracted from the adaptive threshold mean.")
    ] = 15.0,
    ocr: Annotated[
        bool,
        typer.Option("--ocr/--no-ocr", help="Detect, group and OCR subtitles, then write result.srt."),
    ] = True,
    ocr_engine: Annotated[
        str, typer.Option("--ocr-engine", help="OCR engine: auto or paddleocr (none disables recognition).")
    ] = "auto",
    ocr_language: Annotated[
        str, typer.Option("--ocr-language", help="PaddleOCR language code: japan, ch, en, korean ...")
    ] = "japan",
    ocr_device: Annotated[
        str,
        typer.Option("--ocr-device", help="Inference device: auto (GPU when CUDA is available), gpu or cpu."),
    ] = "auto",
    ocr_confidence_threshold: Annotated[
        float, typer.Option("--ocr-confidence-threshold", help="Re-run OCR on a fallback frame below this score.")
    ] = 0.85,
    ocr_fallback_frames: Annotated[
        int, typer.Option("--ocr-fallback-frames", help="Extra group frames to try when confidence is low.")
    ] = 2,
    detection_threshold: Annotated[
        float, typer.Option("--detection-threshold", help="Subtitle-present score threshold (0-1).")
    ] = 0.40,
    similarity_threshold: Annotated[
        float, typer.Option("--similarity-threshold", help="Mask IoU needed to keep the same subtitle group.")
    ] = 0.85,
    max_missing_frames: Annotated[
        int, typer.Option("--max-missing-frames", help="Missing-detection frames tolerated inside one group.")
    ] = 2,
    min_group_frames: Annotated[
        int, typer.Option("--min-group-frames", help="Drop groups shorter than this many detected frames.")
    ] = 2,
    merge_gap: Annotated[
        float,
        typer.Option("--merge-gap", help="Merge neighbouring groups with equal text closer than this (seconds)."),
    ] = 0.8,
    merge_similarity: Annotated[
        float, typer.Option("--merge-similarity", help="Text similarity needed to merge neighbouring groups (0-1).")
    ] = 0.8,
    min_cue_duration: Annotated[
        float, typer.Option("--min-cue-duration", help="Drop caption cues shorter than this (seconds).")
    ] = 0.2,
) -> None:
    """Decode every frame, index PTS, and optionally crop the subtitle region."""
    if input_path is None and from_crops is None:
        raise typer.BadParameter("--input is required unless --from-crops points at an existing task folder")
    if from_crops is not None and output_dir is not None and output_dir.resolve() == from_crops.resolve():
        raise typer.BadParameter("--output must be a new directory when reusing --from-crops")
    for name, value in (
        ("--fallback-fps", fallback_fps),
        ("--gap-warning-seconds", gap_warning_seconds),
        ("--cfr-tolerance", cfr_tolerance),
    ):
        if value is not None and (not math.isfinite(value) or value < 0 or (value == 0 and name != "--cfr-tolerance")):
            raise typer.BadParameter(
                f"{name} must be finite and {'non-negative' if name == '--cfr-tolerance' else 'positive'}"
            )
    if save_frames and from_crops is None:
        echo("Saving every frame as PNG. Use --no-save-frames if you only need the PTS index.")
    crop_config = None
    if crop:
        if not math.isfinite(crop_scale) or not MIN_SCALE <= crop_scale <= MAX_SCALE:
            raise typer.BadParameter(f"--crop-scale must be finite and between {MIN_SCALE} and {MAX_SCALE}")
        try:
            crop_config = CropConfig(
                region=parse_region(crop_box, crop_unit),
                scale=crop_scale,
                variants=parse_variants(crop_variants),
                clahe_clip_limit=clahe_clip_limit,
                adaptive_block_size=adaptive_block_size,
                adaptive_c=adaptive_c,
            )
        except (CropError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        if from_crops is None:
            echo("Writing subtitle crops under crops/ with crop_config.json and crop_preview.jpg.")
    extraction_config = None
    engine = None
    if ocr:
        if not crop and from_crops is None:
            raise typer.BadParameter("--ocr needs an active crop region; drop --no-crop or add --no-ocr")
        for name, value in (
            ("--detection-threshold", detection_threshold),
            ("--similarity-threshold", similarity_threshold),
            ("--ocr-confidence-threshold", ocr_confidence_threshold),
        ):
            if not math.isfinite(value) or not 0.0 < value <= 1.0:
                raise typer.BadParameter(f"{name} must be within (0, 1]")
        if max_missing_frames < 0:
            raise typer.BadParameter("--max-missing-frames cannot be negative")
        if min_group_frames < 1:
            raise typer.BadParameter("--min-group-frames must be at least 1")
        if ocr_fallback_frames < 0:
            raise typer.BadParameter("--ocr-fallback-frames cannot be negative")
        if not math.isfinite(merge_gap) or merge_gap < 0:
            raise typer.BadParameter("--merge-gap must be finite and non-negative")
        if not math.isfinite(merge_similarity) or not 0.0 < merge_similarity <= 1.0:
            raise typer.BadParameter("--merge-similarity must be within (0, 1]")
        if not math.isfinite(min_cue_duration) or min_cue_duration < 0:
            raise typer.BadParameter("--min-cue-duration must be finite and non-negative")
        if not ocr_language.strip():
            raise typer.BadParameter("--ocr-language cannot be empty")
        try:
            extraction_config = ExtractionConfig(
                detection=DetectionConfig(threshold=detection_threshold),
                grouping=GroupingConfig(
                    similarity_threshold=similarity_threshold,
                    max_missing_frames=max_missing_frames,
                    min_group_frames=min_group_frames,
                ),
                ocr_enabled=True,
                confidence_threshold=ocr_confidence_threshold,
                fallback_frames=ocr_fallback_frames,
                merge_gap=merge_gap,
                merge_similarity=merge_similarity,
                min_cue_duration=min_cue_duration,
            )
        except (DetectionError, GroupingError, ExtractionError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        if ocr_engine.strip().lower() not in {"none", "off", "disabled"}:
            echo(
                f"Initialising OCR engine '{ocr_engine}' (language {ocr_language}, device {ocr_device}); "
                "first run may download models."
            )
            try:
                engine = create_ocr_engine(ocr_engine, ocr_language, ocr_device)
                echo(f"OCR device: {getattr(engine, 'device', 'n/a')}")
            except OcrError as exc:
                typer.echo(f"OCR unavailable: {exc}", err=True)
                raise typer.Exit(1) from exc
    if from_crops is not None:
        if extraction_config is None:
            raise typer.BadParameter("--from-crops needs --ocr: it only re-runs detection, grouping and OCR")
        target = output_dir or (from_crops.expanduser().resolve().parent.with_name(from_crops.name + "_ocr"))
        started = time.monotonic()
        echo(f"Reusing crops in {from_crops}; decoding is skipped.")
        bar = ProgressLine()
        try:

            def crop_update(completed: int, expected: int | None) -> None:
                bar.update(completed, expected)

            reused = analyze_crops(
                from_crops,
                output_dir=target,
                extraction_config=extraction_config,
                ocr_engine=engine,
                on_progress=crop_update,
            )
            bar.done()
        except VideoAnalysisError as exc:
            echo(f"Crops reuse failed: {exc}")
            raise typer.Exit(1) from exc
        summary = json.loads((reused / "result.json").read_text(encoding="utf-8"))["summary"]
        echo(
            f"Stages 2-5 completed in {time.monotonic() - started:.0f}s: "
            f"{summary['groups']} groups ({summary['groups_with_text']} with text), "
            f"{summary['srt_cues']} SRT cues, {summary['ocr_calls']} OCR calls"
        )
        echo(f"SRT: {reused / 'result.srt'} | Groups: groups/groups.json")
        return
    assert input_path is not None
    started = time.monotonic()
    bar = ProgressLine()

    def update(completed: int, expected: int | None) -> None:
        bar.update(completed, expected)

    try:
        result = analyze_video(
            input_path,
            output_dir=output_dir,
            save_frames=save_frames,
            fallback_fps=fallback_fps,
            gap_warning_seconds=gap_warning_seconds,
            cfr_tolerance=cfr_tolerance,
            crop_config=crop_config,
            extraction_config=extraction_config,
            ocr_engine=engine,
            on_progress=update,
        )
        bar.done()
    except VideoAnalysisError as exc:
        echo(f"Stage 0 failed: {exc}")
        raise typer.Exit(1) from exc
    info = result.video_info
    echo(f"Stage 0 completed: {info['frame_count']} frames, frame rate mode: {info['frame_rate_mode']}")
    echo(f"Output: {result.output_dir}")
    echo("Index: frame_index.csv | Metadata: video_info.json | State: task_status.json")
    crop_info = info.get("crop")
    if crop_info:
        region = crop_info["resolved_pixels"]
        echo(
            f"Stage 1 completed: {crop_info['crop_count']} crops, "
            f"{region['width']}x{region['height']} -> {crop_info['output_width']}x{crop_info['output_height']}, "
            f"variants: {', '.join(crop_info['variants']) or 'none'}"
        )
        echo(f"Crops: {result.output_dir / 'crops'} | Config: crop_config.json | Preview: crop_preview.jpg")
    extraction = info.get("extraction")
    if extraction:
        echo(
            f"Stages 2-5 completed: {extraction['groups']} groups "
            f"({extraction['groups_with_text']} with text), "
            f"{extraction['detected_frames']}/{extraction['processed_frames']} frames detected, "
            f"{extraction['ocr_calls']} OCR calls"
        )
        echo(f"SRT: {result.output_dir / 'result.srt'} | Groups: groups/groups.json")
    anomalies = info["anomaly_counts"]
    if anomalies:
        echo(f"Timing/decode notices: {sum(anomalies.values())} ({anomalies})")
        echo("Inspect timestamp_anomalies.jsonl and analysis.log before using the timeline.")


if __name__ == "__main__":
    app()
