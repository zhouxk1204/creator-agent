"""Stages 2-5 without decoding: re-run detection/OCR on an existing crops folder.

Stage 0/1 already wrote ``crops/000130.png`` plus ``frame_index.csv``. When only
the detector or OCR changed there is no reason to decode the whole video again:
this entry point reads the PNGs, marks frames that contain text, groups equal
lines, OCRs each group and writes a fresh ``result.srt``.
"""

import csv
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from creator_agent.subtitle_extractor.extraction import ExtractionConfig, SubtitleExtractor
from creator_agent.subtitle_extractor.ocr import OcrEngine
from creator_agent.subtitle_extractor.timeline import VideoAnalysisError


def _write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_frame_times(crops_dir: Path) -> list[tuple[float, float]]:
    """Return ``(pts_time, duration)`` per frame from the stage-0 index."""
    index_file = crops_dir / "frame_index.csv"
    if not index_file.is_file():
        raise VideoAnalysisError(f"Crops folder has no frame_index.csv: {crops_dir}")
    times: list[tuple[float, float]] = []
    with index_file.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            frame_index = int(row["frame_index"])
            if frame_index != len(times):
                raise VideoAnalysisError(f"frame_index.csv is not contiguous at frame {frame_index}")
            times.append((float(row["pts_time"]), float(row["duration"])))
    if not times:
        raise VideoAnalysisError("frame_index.csv is empty")
    return times


def _crop_files(crops_dir: Path) -> list[Path]:
    folder = crops_dir / "crops"
    if not folder.is_dir():
        raise VideoAnalysisError(f"Crops folder has no crops/ directory: {folder}")
    files = sorted(folder.glob("*.png"))
    if not files:
        raise VideoAnalysisError(f"Crops folder is empty: {folder}")
    return files


def analyze_crops(
    crops_dir: Path,
    *,
    output_dir: Path,
    extraction_config: ExtractionConfig,
    ocr_engine: OcrEngine | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Detect, group and OCR subtitles from existing crops; returns the output dir."""
    crops_dir = crops_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    try:
        if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
            raise VideoAnalysisError(
                f"Output path is not an empty directory: {output_dir}. Choose a different --output directory."
            )
        files = _crop_files(crops_dir)
        times = read_frame_times(crops_dir)
        if len(files) != len(times):
            raise VideoAnalysisError(f"{len(files)} crops but {len(times)} frame index rows in {crops_dir}")
        info_path = crops_dir / "video_info.json"
        video_info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
        width = int(video_info.get("width") or 0)
        height = int(video_info.get("height") or 0)
        if not width or not height:
            with Image.open(files[0]) as probe:
                info_width, info_height = probe.size
            # Crop width is not the video width; scaling only affects debug panels,
            # so fall back to the crop size and let the extractor use it verbatim.
            width, height = info_width, info_height
    except VideoAnalysisError:
        raise
    except Exception as exc:
        raise VideoAnalysisError(f"Cannot reuse crops in {crops_dir}: {exc}") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    task = {
        "stage": "stage2-5 (crops reuse)",
        "status": "running",
        "input_path": str(crops_dir),
        "output_path": str(output_dir),
        "options": {"extraction": extraction_config.as_dict()},
    }
    try:
        with (output_dir / "task_status.json").open("x", encoding="utf-8") as task_file:
            task_file.write(json.dumps(task, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as exc:
        raise VideoAnalysisError(f"Another task already owns the output directory: {output_dir}") from exc

    logger = logging.Logger("subtitle_extractor.crops")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(output_dir / "analysis.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    if os.environ.get("SUBTITLE_CONSOLE_LOG") == "1":

        class _GroupOnly(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                message = record.getMessage()
                return record.levelno >= logging.WARNING or message.startswith("Group ")

        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
        console.addFilter(_GroupOnly())
        logger.addHandler(console)

    extractor = SubtitleExtractor(
        output_dir,
        extraction_config,
        frame_width=width,
        frame_height=height,
        logger=logger,
        engine=ocr_engine,
        input_path=crops_dir,
    )
    try:
        logger.info("Stages 2-5 from existing crops: %d images under %s", len(files), crops_dir)
        for position, path in enumerate(files):
            frame_index = int(path.stem)
            if frame_index >= len(times):
                raise VideoAnalysisError(f"Crop {path.name} has no timing row")
            with Image.open(path) as image:
                extractor.observe(frame_index, image.convert("RGB"))
            if on_progress is not None:
                on_progress(position + 1, len(files))
        summary = extractor.finish(times)
    except (Exception, KeyboardInterrupt) as exc:
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        logger.exception("Crops reuse %s; completed data is preserved", status)
        try:
            extractor.close(status)
        except Exception:
            logger.exception("Failed to write partial subtitle artifacts")
        task.update({"status": status, "error": str(exc) or type(exc).__name__})
        _write_json(output_dir / "task_status.json", task)
        handler.close()
        raise

    task.update(
        {
            "status": "completed",
            "decoded_frames": len(files),
            "index_records": len(times),
            "extraction": {
                key: summary[key] for key in ("groups", "groups_with_text", "detected_frames", "ocr_calls", "srt_cues")
            },
        }
    )
    _write_json(output_dir / "task_status.json", task)
    _write_json(
        output_dir / "video_info.json",
        {
            "video_path": video_info.get("video_path"),
            "width": width,
            "height": height,
            "frame_count": len(files),
            "crops_source": str(crops_dir),
            "extraction": summary,
        },
    )
    logger.info(
        "Stages 2-5 completed: %d groups, %d with text, %d SRT cues, %d OCR calls",
        summary["groups"],
        summary["groups_with_text"],
        summary["srt_cues"],
        summary["ocr_calls"],
    )
    handler.close()
    return output_dir
