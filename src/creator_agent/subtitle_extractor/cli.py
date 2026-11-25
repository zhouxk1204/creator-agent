"""Independent CLI for the staged anime hard-subtitle extractor."""

import math
from pathlib import Path
from typing import Annotated

import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from creator_agent.subtitle_extractor.analysis import analyze_video
from creator_agent.subtitle_extractor.timeline import VideoAnalysisError

app = typer.Typer(help="Anime hard-subtitle extractor — stage 0: video decoding and PTS frame index.")


@app.command()
def main(
    input_path: Annotated[
        Path,
        typer.Option("--input", "-i", exists=True, file_okay=True, dir_okay=False, help="Input video file."),
    ],
    output_dir: Annotated[
        Path | None, typer.Option("--output", "-o", help="Empty task directory (default: output/<video stem>).")
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
) -> None:
    """Decode every frame in display order; no subtitle detection or OCR yet."""
    for name, value in (
        ("--fallback-fps", fallback_fps),
        ("--gap-warning-seconds", gap_warning_seconds),
        ("--cfr-tolerance", cfr_tolerance),
    ):
        if value is not None and (not math.isfinite(value) or value < 0 or (value == 0 and name != "--cfr-tolerance")):
            raise typer.BadParameter(
                f"{name} must be finite and {'non-negative' if name == '--cfr-tolerance' else 'positive'}"
            )
    if save_frames:
        typer.echo("Saving every frame as PNG. Use --no-save-frames if you only need the PTS index.")
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed:.0f} frames"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Decoding video", total=None)

            def update(completed: int, expected: int | None) -> None:
                progress.update(task, completed=completed, total=expected)

            result = analyze_video(
                input_path,
                output_dir=output_dir,
                save_frames=save_frames,
                fallback_fps=fallback_fps,
                gap_warning_seconds=gap_warning_seconds,
                cfr_tolerance=cfr_tolerance,
                on_progress=update,
            )
            count = result.video_info["frame_count"]
            progress.update(task, completed=count, total=count)
    except VideoAnalysisError as exc:
        typer.echo(f"Stage 0 failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    info = result.video_info
    typer.echo(f"Stage 0 completed: {info['frame_count']} frames, frame rate mode: {info['frame_rate_mode']}")
    typer.echo(f"Output: {result.output_dir}")
    typer.echo("Index: frame_index.csv | Metadata: video_info.json | State: task_status.json")
    anomalies = info["anomaly_counts"]
    if anomalies:
        typer.echo(f"Timing/decode notices: {sum(anomalies.values())} ({anomalies})")
        typer.echo("Inspect timestamp_anomalies.jsonl and analysis.log before using the timeline.")


if __name__ == "__main__":
    app()
