"""Stages 2-5 orchestration: detection, grouping, per-group OCR and SRT output."""

import difflib
import json
import logging
import math
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from creator_agent.subtitle_extractor.detection import (
    DetectionConfig,
    FrameDetection,
    detect_subtitle,
    sharpness,
)
from creator_agent.subtitle_extractor.grouping import GroupingConfig, GroupTracker, SubtitleGroup
from creator_agent.subtitle_extractor.ocr import OcrEngine, OcrReading
from creator_agent.subtitle_extractor.srt import SrtCue, build_srt

_TEXT_CHARACTER = re.compile("[0-9A-Za-z\u00c0-\u024f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_CJK_TEXT = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def normalize_caption(text: str) -> str:
    """Fold width, case, spacing and punctuation so repeated lines compare equal."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\W+", "", folded)


def caption_similarity(left: str, right: str) -> float:
    """Compare two OCR strings after normalisation; 1.0 means identical."""
    a, b = normalize_caption(left), normalize_caption(right)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


_CJK_SPACE = re.compile(r"([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff])\s+([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff])")
_TRAILING_PUNCT = re.compile(r"\s*[.,;:!?]+\s*$")


def clean_caption(text: str) -> str:
    """Tidy OCR output without changing its wording."""
    text = text.strip()
    while _CJK_SPACE.search(text):
        text = _CJK_SPACE.sub(r"\1\2", text)
    trailing = _TRAILING_PUNCT.search(text)
    if trailing is not None and len(trailing.group().strip()) == 1 and is_meaningful_text(text):
        text = text[: trailing.start()]
    return text.strip()


def is_meaningful_text(text: str) -> bool:
    """Reject OCR noise such as ``((`` or stray punctuation."""
    return bool(_TEXT_CHARACTER.search(text))


class ExtractionError(Exception):
    """The extraction stage cannot continue."""


@dataclass(frozen=True)
class ExtractionConfig:
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    grouping: GroupingConfig = field(default_factory=GroupingConfig)
    ocr_enabled: bool = True
    confidence_threshold: float = 0.85
    fallback_frames: int = 2
    min_text_length: int = 1
    merge_gap: float = 0.8
    merge_similarity: float = 0.8
    low_confidence_floor: float = 0.6
    low_confidence_min_duration: float = 0.2
    min_cue_duration: float = 0.2
    short_cue_max_chars: int = 2
    short_cue_min_duration: float = 0.05
    rescue_enabled: bool = True
    rescue_min_score: float = 0.15
    rescue_min_confidence: float = 0.85
    rescue_max_chars: int = 8
    rescue_min_height_ratio: float = 0.12
    rescue_padding: int = 6
    rescue_stroke_difference: int = 45
    rescue_score: float = 0.45

    def __post_init__(self) -> None:
        if not math.isfinite(self.confidence_threshold) or not 0.0 <= self.confidence_threshold <= 1.0:
            raise ExtractionError("OCR confidence threshold must be within 0..1")
        if self.fallback_frames < 0:
            raise ExtractionError("OCR fallback frame count cannot be negative")
        if self.min_text_length < 1:
            raise ExtractionError("minimum text length must be at least 1")
        if not math.isfinite(self.merge_gap) or self.merge_gap < 0:
            raise ExtractionError("caption merge gap must be a finite, non-negative number of seconds")
        if not math.isfinite(self.merge_similarity) or not 0.0 < self.merge_similarity <= 1.0:
            raise ExtractionError("caption merge similarity must be within (0, 1]")
        if not math.isfinite(self.low_confidence_floor) or not 0.0 <= self.low_confidence_floor <= 1.0:
            raise ExtractionError("low-confidence floor must be within 0..1")
        if not math.isfinite(self.low_confidence_min_duration) or self.low_confidence_min_duration < 0:
            raise ExtractionError("low-confidence minimum duration must be finite and non-negative")
        if not math.isfinite(self.min_cue_duration) or self.min_cue_duration < 0:
            raise ExtractionError("minimum cue duration must be finite and non-negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "detection_threshold": self.detection.threshold,
            "similarity_threshold": self.grouping.similarity_threshold,
            "max_missing_frames": self.grouping.max_missing_frames,
            "min_group_frames": self.grouping.min_group_frames,
            "ocr_enabled": self.ocr_enabled,
            "confidence_threshold": self.confidence_threshold,
            "fallback_frames": self.fallback_frames,
            "min_text_length": self.min_text_length,
            "merge_gap": self.merge_gap,
            "merge_similarity": self.merge_similarity,
            "low_confidence_floor": self.low_confidence_floor,
            "low_confidence_min_duration": self.low_confidence_min_duration,
            "min_cue_duration": self.min_cue_duration,
        }


@dataclass
class ExtractedGroup:
    group_id: int
    group: SubtitleGroup
    text: str
    confidence: float
    engine: str
    status: str
    folder: str
    ocr_calls: int
    start_time: float = 0.0
    end_time: float = 0.0
    in_srt: bool = True
    readings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "start_frame": self.group.start_frame,
            "end_frame": self.group.end_frame,
            "frame_count": self.group.frame_count,
            "missing_frames": self.group.missing_frames,
            "start_pts": round(self.start_time, 6),
            "end_pts": round(self.end_time, 6),
            "duration": round(self.duration, 6),
            "selected_frames": self.group.selected_frames,
            "text": self.text or None,
            "confidence": round(self.confidence, 4) if self.text else None,
            "status": self.status,
            "in_srt": self.in_srt,
            "ocr_engine": self.engine,
            "folder": self.folder,
            # Per-candidate OCR results live here instead of a file per group.
            "ocr": self.readings,
        }


def rescue_detection(
    image: Image.Image,
    bgr: np.ndarray,
    detection: FrameDetection,
    engine: OcrEngine,
    config: ExtractionConfig,
) -> tuple[FrameDetection, OcrReading] | None:
    """Second chance for a borderline frame: read the candidate box with OCR.

    Coloured captions over a similarly bright background score too low for the
    brightness detector, yet the recogniser still reads them cleanly. Rather
    than lowering the detection threshold (which lets background texture in),
    OCR the candidate box and promote the frame only when the read is short,
    confident and looks like caption glyphs.
    """
    if not config.rescue_enabled or detection.has_subtitle or detection.bbox is None:
        return None
    if detection.score < config.rescue_min_score:
        return None
    left, top, box_width, box_height = detection.bbox
    height, width = bgr.shape[:2]
    if box_height / height < config.rescue_min_height_ratio:
        return None
    try:
        # Read the whole band, not the candidate box: the brightness detector
        # often boxes one stroke of a coloured glyph, while the recogniser needs
        # the full line to return anything.
        reading = engine.read(image)
    except Exception:  # a rescue attempt must never abort the run
        return None
    text = clean_caption(reading.text)
    flattened = normalize_caption(text)
    if (
        not text
        or not is_meaningful_text(text)
        or len(text) > config.rescue_max_chars
        or reading.confidence < config.rescue_min_confidence
        or not _TEXT_CHARACTER.search(text)
        # "儿儿": a background shape read twice as the same glyph.
        or (len(flattened) > 1 and len(set(flattened)) <= 1)
    ):
        return None

    background = cv2.medianBlur(bgr, 31)
    difference = np.max(np.abs(bgr.astype(np.int16) - background.astype(np.int16)), axis=2)
    strokes = (difference >= config.rescue_stroke_difference).astype(np.uint8) * 255
    strokes = cv2.morphologyEx(strokes, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    # Keep only the vertical band the detector pointed at, so background
    # elsewhere in the crop cannot shift the grouping similarity.
    mask = np.zeros((height, width), dtype=np.uint8)
    band_top = max(0, top - config.rescue_padding)
    band_bottom = min(height, top + box_height + config.rescue_padding)
    mask[band_top:band_bottom] = strokes[band_top:band_bottom]
    if np.count_nonzero(mask) < 30:
        return None

    promoted = FrameDetection(
        score=round(max(detection.score, config.rescue_score), 4),
        has_subtitle=True,
        components=detection.components,
        coverage=detection.coverage,
        density=detection.density,
        bright_fraction=detection.bright_fraction,
        bbox=detection.bbox,
        mask=mask,
    )
    return promoted, reading


def is_srt_worthy(text: str, status: str, confidence: float, duration: float, config: ExtractionConfig) -> bool:
    """Decide whether one recognised group belongs in the SRT."""
    if not text or status not in {"ok", "low_confidence"}:
        return False
    minimum = config.min_cue_duration
    if len(normalize_caption(text)) <= config.short_cue_max_chars:
        # "嗯" / "哦?" only live for a handful of frames; a blob of background
        # text does not survive the vote, so a short hold is still a caption.
        minimum = min(minimum, config.short_cue_min_duration)
    if duration < minimum:
        return False
    if not _CJK_TEXT.search(text) and len(normalize_caption(text)) < 3:
        # "0", "Y", "CLe": one or two latin/digit glyphs are almost always a
        # piece of background, not a caption.
        return False
    if status == "ok":
        return True
    return (
        confidence >= config.low_confidence_floor
        and duration >= config.low_confidence_min_duration
        and len(text.strip()) >= 2
        # "儿儿": the same glyph twice is a background shape read as text.
        and len(set(normalize_caption(text))) > 1
    )


def _vote_text(readings: list[tuple[int, OcrReading]]) -> tuple[str, float]:
    """Pick the caption that most frames agree on.

    A single OCR pass can misread a stroke or pick up a background artefact;
    three frames of the same line rarely fail the same way, so the largest
    agreement cluster wins and the best-scoring member supplies the wording.
    """
    usable = [
        (index, reading) for index, reading in readings if reading.text.strip() and is_meaningful_text(reading.text)
    ]
    if not usable:
        return "", 0.0
    clusters: list[list[tuple[int, OcrReading]]] = []
    for entry in usable:
        for cluster in clusters:
            if caption_similarity(cluster[0][1].text, entry[1].text) >= 0.7:
                cluster.append(entry)
                break
        else:
            clusters.append([entry])
    best = max(clusters, key=lambda cluster: (len(cluster), sum(item[1].confidence for item in cluster)))
    representative = max(best, key=lambda item: item[1].confidence)
    text = clean_caption(representative[1].text)
    confidence = representative[1].confidence
    if not text or not is_meaningful_text(text):
        return "", 0.0
    return text, confidence


class SubtitleExtractor:
    def __init__(
        self,
        output_dir: Path,
        config: ExtractionConfig,
        *,
        frame_width: int,
        frame_height: int,
        logger: logging.Logger,
        engine: OcrEngine | None,
        input_path: Path,
    ) -> None:
        self.output_dir = output_dir
        self.config = config
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.logger = logger
        self.engine = engine
        self.input_path = input_path

        (output_dir / "groups").mkdir(parents=True, exist_ok=True)
        (output_dir / "ocr").mkdir(exist_ok=True)
        self._tracker = GroupTracker(config.grouping, self._close_group)
        self._groups: list[ExtractedGroup] = []
        self._next_group_id = 0
        self._detected_frames = 0
        self._processed_frames = 0
        self._ocr_calls = 0
        self._ocr_failures = 0
        self._merged_count = 0
        self._closed = False

    def observe(self, frame_index: int, image: Image.Image) -> None:
        self._processed_frames += 1
        bgr = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        detection = detect_subtitle(bgr, self.config.detection)
        if not detection.has_subtitle and self.engine is not None:
            rescued = rescue_detection(image, bgr, detection, self.engine, self.config)
            if rescued is not None:
                detection, reading = rescued
                self._ocr_calls += 1
                self.logger.debug(
                    "Rescued frame %d as %r (conf %.2f, score %.2f)",
                    frame_index,
                    reading.text,
                    reading.confidence,
                    detection.score,
                )
        sharp = sharpness(bgr, detection.mask) if detection.has_subtitle else 0.0
        if detection.has_subtitle:
            self._detected_frames += 1
            self._tracker.add(frame_index, detection.mask, image, sharp)
        else:
            self._tracker.skip(frame_index)

    def _close_group(self, group: SubtitleGroup) -> None:
        self._next_group_id += 1
        group_id = self._next_group_id
        folder_name = f"group_{group_id:04d}"
        folder = self.output_dir / "groups" / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        for index, image, _ in group.best_frames:
            image.save(folder / f"frame_{index:06d}.png")

        readings: list[dict[str, Any]] = []
        text, confidence, status, engine_name = "", 0.0, "ocr_disabled", self.engine.name if self.engine else "none"
        calls = 0
        if self.engine is not None:
            candidates = group.ocr_frames(1 + self.config.fallback_frames)
            votes: list[tuple[int, OcrReading]] = []
            failed = False
            for index, image, _ in candidates:
                calls += 1
                self._ocr_calls += 1
                try:
                    reading = self.engine.read(image)
                except Exception as exc:  # OCR failures must not abort the whole task
                    self._ocr_failures += 1
                    failed = True
                    self.logger.exception("OCR failed at frame %d", index)
                    readings.append({"frame_index": index, "error": str(exc)})
                    continue
                readings.append({"frame_index": index, **reading.as_dict()})
                votes.append((index, reading))
            text, confidence = _vote_text(votes)
            if text and len(text.strip()) < self.config.min_text_length:
                text, confidence = "", 0.0
            if not text:
                status = "ocr_error" if failed and not votes else "no_text"
            else:
                status = "ok" if confidence >= self.config.confidence_threshold else "low_confidence"
        if not text:
            # Empty groups stay in memory for merging bookkeeping but are not
            # published: no frame folder, no OCR detail, no entry in groups.json.
            shutil.rmtree(folder, ignore_errors=True)
            folder_name = ""
        extracted = ExtractedGroup(
            group_id=group_id,
            group=group,
            text=text,
            confidence=confidence,
            engine=engine_name,
            status=status,
            folder=folder_name,
            ocr_calls=calls,
            readings=readings,
        )
        self._groups.append(extracted)
        self.logger.debug(
            "Group %04d frames %d-%d (%d), status %s, text %r",
            group_id,
            group.start_frame,
            group.end_frame,
            group.frame_count,
            status,
            text[:40],
        )

    def finish(self, times: list[tuple[float, float]]) -> dict[str, Any]:
        if self._closed:
            raise ExtractionError("Extractor already finished")
        self._tracker.finish()
        if not times:
            raise ExtractionError("No frame timing available for subtitle groups")
        for item in self._groups:
            if item.group.end_frame >= len(times):
                raise ExtractionError(f"Group {item.group_id} points past the decoded frame count")
            start, _ = times[item.group.start_frame]
            end_start, end_duration = times[item.group.end_frame]
            item.start_time = max(0.0, start)
            item.end_time = max(item.start_time + 0.001, end_start + end_duration)
        self._merge_groups()
        for item in self._groups:
            item.in_srt = self._is_srt_worthy(item)

        # Only groups that produced text are published; empty ones exist purely
        # for the grouping/merging bookkeeping above.
        published = [item for item in self._groups if item.text]
        groups_json = [item.as_dict() for item in published]
        (self.output_dir / "groups" / "groups.json").write_text(
            json.dumps(groups_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._write_frame_text(times)
        cues = [SrtCue(item.start_time, item.end_time, item.text, item.group_id) for item in published if item.in_srt]
        srt_text = build_srt(cues)
        (self.output_dir / "result.srt").write_text(srt_text, encoding="utf-8")
        summary = self.summary()
        result = {
            "stage": "stage5",
            "status": "completed",
            "input_path": str(self.input_path),
            "srt": "result.srt",
            "groups_file": "groups/groups.json",
            "ocr_engine": self.engine.name if self.engine else None,
            "config": self.config.as_dict(),
            "summary": summary,
            "groups": groups_json,
        }
        (self.output_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._release()
        self._closed = True
        return summary

    def _write_frame_text(self, times: list[tuple[float, float]]) -> None:
        """Dump every frame that carries recognised text, one record per frame.

        Frames without text are omitted, so the file is a direct answer to
        "which frame holds which line" and can be diffed against another run.
        """
        records: list[dict[str, Any]] = []
        for item in self._groups:
            if not item.text or item.status not in {"ok", "low_confidence"}:
                continue
            missing = int(item.group.missing_frames or 0)
            last_frame = item.group.end_frame - missing
            for frame in range(item.group.start_frame, last_frame + 1):
                start = times[frame][0] if 0 <= frame < len(times) else 0.0
                records.append(
                    {
                        "frame": frame,
                        "time": round(start, 3),
                        "text": item.text,
                        "confidence": round(item.confidence, 4),
                        "group_id": item.group_id,
                        "in_srt": item.in_srt,
                    }
                )
        path = self.output_dir / "ocr" / "frames.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _is_srt_worthy(self, item: ExtractedGroup) -> bool:
        """Confident text always ships; weak or unreadably short fragments are dropped."""
        return is_srt_worthy(item.text, item.status, item.confidence, item.duration, self.config)

    def _merge_groups(self) -> None:
        """Join neighbouring groups that OCR to the same line.

        Mask similarity splits a caption whenever the text animates or the
        background shifts, so the same line can arrive as a chain of short
        groups. Neighbouring groups with near-identical text and a short gap
        are one caption event; a junk-prefixed OCR variant merges back into the
        clean line it belongs to.

        The comparison skips unreadable groups in between: a couple of frames
        the detector missed must not cut one caption into two cues.
        """
        merged: list[ExtractedGroup] = []
        for item in self._groups:
            previous = next((entry for entry in reversed(merged) if entry.text), None)
            if (
                previous is not None
                and item.text
                and item.start_time - previous.end_time <= self.config.merge_gap
                and caption_similarity(previous.text, item.text) >= self.config.merge_similarity
            ):
                self._absorb(previous, item)
                self._merged_count += 1
                continue
            merged.append(item)
        self._groups = merged

    def _absorb(self, target: ExtractedGroup, other: ExtractedGroup) -> None:
        frames = sorted(
            [*target.group.best_frames, *other.group.best_frames],
            key=lambda entry: entry[0],
        )[: self.config.grouping.keep_frames]
        target.group = SubtitleGroup(
            start_frame=target.group.start_frame,
            end_frame=other.group.end_frame,
            frame_count=target.group.frame_count + other.group.frame_count,
            missing_frames=target.group.missing_frames + other.group.missing_frames,
            best_frames=frames,
        )
        target.end_time = max(target.end_time, other.end_time)
        target.ocr_calls += other.ocr_calls
        target.readings.extend(other.readings)
        if other.confidence > target.confidence:
            # Junk-prefixed OCR variants score lower than the clean reading, so
            # the highest-confidence text wins instead of the longest one.
            target.text = other.text
            target.confidence = other.confidence
        if other.status == "ok" or target.status == "ok":
            target.status = "ok"
        elif other.status == "low_confidence":
            target.status = "low_confidence"

    def summary(self) -> dict[str, Any]:
        detected = [item for item in self._groups if item.status in {"ok", "low_confidence"}]
        return {
            "enabled": True,
            "detected_frames": self._detected_frames,
            "processed_frames": self._processed_frames,
            "detection_rate": (
                round(self._detected_frames / self._processed_frames, 4) if self._processed_frames else 0.0
            ),
            "groups": len(self._groups),
            "groups_with_text": len(detected),
            "srt_cues": sum(1 for item in self._groups if item.in_srt),
            "merged_groups": self._merged_count,
            "ocr_calls": self._ocr_calls,
            "ocr_failures": self._ocr_failures,
            "ocr_device": getattr(self.engine, "summary", None) if self.engine else None,
            "confidence_threshold": self.config.confidence_threshold,
            "srt": "result.srt",
        }

    def close(self, status: str) -> None:
        """Release partial data after a failure; never pretends the task succeeded."""
        if self._closed:
            return
        self._release()
        self._closed = True
        published = [item.as_dict() for item in self._groups if item.text]
        if published:
            (self.output_dir / "groups" / "groups.json").write_text(
                json.dumps({"status": status, "groups": published}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def _release(self) -> None:
        """Nothing to flush: all per-frame detail is kept in groups.json."""
