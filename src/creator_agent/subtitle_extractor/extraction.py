"""Stages 2-5 orchestration: detection, grouping, per-group OCR and SRT output."""

import csv
import difflib
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from creator_agent.subtitle_extractor.detection import (
    DetectionConfig,
    detect_subtitle,
    sharpness,
)
from creator_agent.subtitle_extractor.grouping import GroupingConfig, GroupTracker, SubtitleGroup
from creator_agent.subtitle_extractor.ocr import OcrEngine, OcrReading
from creator_agent.subtitle_extractor.srt import SrtCue, build_srt

DETECTION_SAMPLES = 6
GROUP_SAMPLES = 12

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
        }


def is_srt_worthy(text: str, status: str, confidence: float, duration: float, config: ExtractionConfig) -> bool:
    """Decide whether one recognised group belongs in the SRT."""
    if not text or status not in {"ok", "low_confidence"}:
        return False
    if duration < config.min_cue_duration:
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

        (output_dir / "detection").mkdir(parents=True, exist_ok=True)
        (output_dir / "debug").mkdir(exist_ok=True)
        (output_dir / "groups").mkdir(exist_ok=True)
        (output_dir / "ocr").mkdir(exist_ok=True)
        self._detection_file = (output_dir / "detection" / "frame_detection.csv").open(
            "w", encoding="utf-8", newline=""
        )
        self._detection_writer = csv.writer(self._detection_file)
        self._detection_writer.writerow(
            [
                "frame_index",
                "has_subtitle",
                "score",
                "components",
                "coverage",
                "density",
                "bright_fraction",
                "text_bbox",
                "sharpness",
            ]
        )
        self._similarity_file = (output_dir / "debug" / "similarity.csv").open("w", encoding="utf-8", newline="")
        self._similarity_writer = csv.writer(self._similarity_file)
        self._similarity_writer.writerow(["frame_index", "previous_frame", "iou", "threshold", "decision"])
        self._tracker = GroupTracker(config.grouping, self._close_group)
        self._groups: list[ExtractedGroup] = []
        self._next_group_id = 0
        self._detected_frames = 0
        self._processed_frames = 0
        self._ocr_calls = 0
        self._ocr_failures = 0
        self._merged_count = 0
        self._detection_samples: list[tuple[int, Image.Image, np.ndarray | None, float]] = []
        self._sample_random = np.random.default_rng(0)
        self._closed = False

    def observe(self, frame_index: int, image: Image.Image) -> None:
        self._processed_frames += 1
        bgr = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        detection = detect_subtitle(bgr, self.config.detection)
        sharp = sharpness(bgr, detection.mask) if detection.has_subtitle else 0.0
        row = detection.as_row()
        self._detection_writer.writerow(
            [
                frame_index,
                row["has_subtitle"],
                row["score"],
                row["components"],
                row["coverage"],
                row["density"],
                row["bright_fraction"],
                row["text_bbox"],
                f"{sharp:.4f}",
            ]
        )
        if detection.has_subtitle:
            self._detected_frames += 1
            previous = self._tracker.last_index
            similarity = self._tracker.add(frame_index, detection.mask, image, sharp)
            decision = (
                "start"
                if similarity is None
                else ("same" if similarity >= self.config.grouping.similarity_threshold else "new")
            )
            self._similarity_writer.writerow(
                [
                    frame_index,
                    previous if previous is not None else "",
                    "" if similarity is None else f"{similarity:.4f}",
                    f"{self.config.grouping.similarity_threshold:.4f}",
                    decision,
                ]
            )
            self._keep_detection_sample(frame_index, image, detection.mask, detection.score)
        else:
            self._tracker.skip(frame_index)

    def _keep_detection_sample(
        self, frame_index: int, image: Image.Image, mask: np.ndarray | None, score: float
    ) -> None:
        samples = self._detection_samples
        if len(samples) < DETECTION_SAMPLES:
            samples.append((frame_index, image.copy(), None if mask is None else mask.copy(), score))
        elif self._sample_random.random() * self._detected_frames < DETECTION_SAMPLES:
            position = int(self._sample_random.integers(0, DETECTION_SAMPLES))
            samples[position] = (frame_index, image.copy(), None if mask is None else mask.copy(), score)

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
            if readings:
                (self.output_dir / "ocr" / f"{folder_name}.json").write_text(
                    json.dumps(
                        {"group_id": group_id, "candidates": readings, "status": status, "votes": len(votes)},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
        extracted = ExtractedGroup(
            group_id=group_id,
            group=group,
            text=text,
            confidence=confidence,
            engine=engine_name,
            status=status,
            folder=folder_name,
            ocr_calls=calls,
        )
        self._groups.append(extracted)
        self.logger.info(
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

        groups_json = [item.as_dict() for item in self._groups]
        (self.output_dir / "groups" / "groups.json").write_text(
            json.dumps(groups_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        cues = [
            SrtCue(item.start_time, item.end_time, item.text, item.group_id) for item in self._groups if item.in_srt
        ]
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
        self._write_visualizations()
        self._release()
        self._closed = True
        return summary

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
            "confidence_threshold": self.config.confidence_threshold,
            "srt": "result.srt",
        }

    def close(self, status: str) -> None:
        """Release partial data after a failure; never pretends the task succeeded."""
        if self._closed:
            return
        self._release()
        self._closed = True
        if self._groups:
            groups_json = [item.as_dict() for item in self._groups]
            (self.output_dir / "groups" / "groups.json").write_text(
                json.dumps({"status": status, "groups": groups_json}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def _release(self) -> None:
        self._detection_file.close()
        self._similarity_file.close()

    def _write_visualizations(self) -> None:
        self._write_detection_visualization()
        self._write_groups_visualization()

    def _write_detection_visualization(self) -> None:
        if not self._detection_samples:
            return
        panels: list[Image.Image] = []
        for frame_index, image, mask, score in sorted(self._detection_samples, key=lambda item: item[0]):
            rgb = np.asarray(image.convert("RGB"))
            if mask is not None:
                overlay = rgb.copy()
                overlay[mask > 0] = (255, 48, 48)
                rgb = cv2.addWeighted(rgb, 0.6, overlay, 0.4, 0)
            display = Image.fromarray(rgb)
            draw = ImageDraw.Draw(display)
            draw.text((6, 4), f"frame {frame_index:06d}  score {score:.2f}", fill=(255, 255, 0))
            panels.append(display)
        self._stack_images(panels, self.output_dir / "debug" / "detection_visualization.jpg")

    def _write_groups_visualization(self) -> None:
        if not self._groups:
            return
        sampled = self._groups[:GROUP_SAMPLES]
        width = min(720, self.frame_width)
        panels: list[Image.Image] = []
        for item in sampled:
            best = item.group.best_frames[0] if item.group.best_frames else None
            if best is None:
                continue
            image = best[1]
            ratio = width / image.width
            display = image.resize((width, max(1, int(round(image.height * ratio)))))
            draw = ImageDraw.Draw(display)
            label = f"#{item.group_id:04d} {item.start_time:.2f}-{item.end_time:.2f}s {item.status} {item.text[:24]}"
            draw.text((4, 2), label, fill=(255, 255, 0))
            panels.append(display)
        self._stack_images(panels, self.output_dir / "debug" / "groups_visualization.jpg")

    @staticmethod
    def _stack_images(panels: list[Image.Image], path: Path) -> None:
        if not panels:
            return
        width = max(panel.width for panel in panels)
        gap = 8
        canvas = Image.new(
            "RGB", (width, sum(panel.height for panel in panels) + gap * (len(panels) + 1)), (16, 16, 16)
        )
        offset = gap
        for panel in panels:
            canvas.paste(panel, (0, offset))
            offset += panel.height + gap
        canvas.save(path, "JPEG", quality=85)
