"""Stage 1: crop the configured subtitle region and emit preprocessed variants."""

import json
import logging
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from creator_agent.subtitle_extractor.timeline import VideoAnalysisError

VARIANT_NAMES = ("gray", "clahe", "otsu", "adaptive")
PREVIEW_SAMPLE_COUNT = 4
MIN_SCALE = 0.1
MAX_SCALE = 8.0


class CropError(VideoAnalysisError):
    """A crop region or frame cannot produce usable stage-1 output."""


def _round_edge(value: float) -> int:
    return math.floor(value + 0.5)


@dataclass(frozen=True)
class PixelBox:
    """A left/top/right/bottom box in original-frame pixels (right/bottom exclusive)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class CropRegion:
    """Subtitle region declared in percent (0-100) or pixels of the source frame."""

    x_start: float
    y_start: float
    x_end: float
    y_end: float
    unit: str = "percent"

    def __post_init__(self) -> None:
        if self.unit not in {"percent", "pixel"}:
            raise CropError(f"Unknown crop unit: {self.unit}. Use 'percent' or 'pixel'.")
        values = (self.x_start, self.y_start, self.x_end, self.y_end)
        if any(not math.isfinite(value) for value in values):
            raise CropError("Crop coordinates must be finite numbers")
        if self.x_start >= self.x_end or self.y_start >= self.y_end:
            raise CropError("Crop region requires x_start < x_end and y_start < y_end")
        if self.unit == "percent" and any(value < 0 or value > 100 for value in values):
            raise CropError("Percent crop coordinates must stay within 0-100")
        if self.unit == "pixel" and any(value < 0 for value in values):
            raise CropError("Pixel crop coordinates cannot be negative")

    def resolve(self, frame_width: int, frame_height: int) -> PixelBox:
        if frame_width <= 0 or frame_height <= 0:
            raise CropError(f"Invalid frame size {frame_width}x{frame_height}")
        if self.unit == "percent":
            left = _round_edge(self.x_start / 100 * frame_width)
            right = _round_edge(self.x_end / 100 * frame_width)
            top = _round_edge(self.y_start / 100 * frame_height)
            bottom = _round_edge(self.y_end / 100 * frame_height)
        else:
            left = _round_edge(self.x_start)
            right = _round_edge(self.x_end)
            top = _round_edge(self.y_start)
            bottom = _round_edge(self.y_end)
        box = PixelBox(left, top, right, bottom)
        if left < 0 or top < 0 or right > frame_width or bottom > frame_height:
            raise CropError(
                f"Crop region {box.as_dict()} exceeds the {frame_width}x{frame_height} frame; "
                "adjust --crop-box or --crop-unit"
            )
        if box.width < 2 or box.height < 2:
            raise CropError(f"Crop region is too small: {box.width}x{box.height}")
        return box

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "x_start": self.x_start,
            "y_start": self.y_start,
            "x_end": self.x_end,
            "y_end": self.y_end,
        }


@dataclass(frozen=True)
class CropConfig:
    region: CropRegion
    scale: float = 1.0
    variants: tuple[str, ...] = VARIANT_NAMES
    clahe_clip_limit: float = 2.0
    clahe_tile_grid: int = 8
    adaptive_block_size: int = 31
    adaptive_c: float = 15.0

    def __post_init__(self) -> None:
        if not isinstance(self.scale, int | float) or not math.isfinite(self.scale):
            raise CropError("crop scale must be a finite number")
        if not MIN_SCALE <= self.scale <= MAX_SCALE:
            raise CropError(f"crop scale must be between {MIN_SCALE} and {MAX_SCALE}")
        unknown = [name for name in self.variants if name not in VARIANT_NAMES]
        if unknown:
            raise CropError(f"Unknown crop variants: {', '.join(unknown)}")
        if not math.isfinite(self.clahe_clip_limit) or self.clahe_clip_limit < 0:
            raise CropError("CLAHE clip limit must be finite and non-negative")
        if self.clahe_tile_grid < 1:
            raise CropError("CLAHE tile grid must be at least 1")
        if self.adaptive_block_size < 3 or self.adaptive_block_size % 2 == 0:
            raise CropError("Adaptive threshold block size must be an odd number >= 3")
        if not math.isfinite(self.adaptive_c):
            raise CropError("Adaptive threshold C must be finite")

    def output_size(self, box: PixelBox) -> tuple[int, int]:
        return max(1, _round_edge(box.width * self.scale)), max(1, _round_edge(box.height * self.scale))

    def as_dict(self) -> dict[str, Any]:
        return {
            "region": self.region.as_dict(),
            "scale": self.scale,
            "variants": list(self.variants),
            "variant_settings": {
                "clahe_clip_limit": self.clahe_clip_limit,
                "clahe_tile_grid": self.clahe_tile_grid,
                "adaptive_block_size": self.adaptive_block_size,
                "adaptive_c": self.adaptive_c,
            },
        }


def parse_region(text: str, unit: str) -> CropRegion:
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError("--crop-box expects four comma-separated numbers: x_start,y_start,x_end,y_end")
    try:
        values = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"--crop-box must contain numbers, got {text!r}") from exc
    return CropRegion(values[0], values[1], values[2], values[3], unit=unit)


def parse_variants(text: str) -> tuple[str, ...]:
    names = [part.strip().lower() for part in text.split(",") if part.strip()]
    if not names or names == ["none"]:
        return ()
    unknown = [name for name in names if name not in VARIANT_NAMES]
    if unknown:
        raise ValueError(
            f"Unknown --crop-variants value(s): {', '.join(unknown)}; choose from {', '.join(VARIANT_NAMES)}"
        )
    return tuple(dict.fromkeys(names))


class CropProcessor:
    """Crop every decoded frame into the same region and record stage-1 artifacts.

    Frame numbering and filenames stay identical to stage 0, so every crop can be
    traced back to one row of ``frame_index.csv``.
    """

    def __init__(
        self,
        output_dir: Path,
        config: CropConfig,
        *,
        frame_width: int,
        frame_height: int,
        logger: logging.Logger,
    ) -> None:
        self.output_dir = output_dir
        self.config = config
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.logger = logger
        self.box = config.region.resolve(frame_width, frame_height)
        self.output_width, self.output_height = config.output_size(self.box)
        self.crop_count = 0
        self.frame_size_mismatch_count = 0
        self._preview_samples: list[tuple[int, Image.Image, Image.Image]] = []
        self._reservoir = random.Random(0)
        self._clahe = cv2.createCLAHE(
            clipLimit=config.clahe_clip_limit,
            tileGridSize=(config.clahe_tile_grid, config.clahe_tile_grid),
        )
        self._crops_dir = output_dir / "crops"
        self._crops_dir.mkdir(parents=True, exist_ok=True)
        for variant in config.variants:
            (self._crops_dir / variant).mkdir()

    def process(self, frame_index: int, image: Image.Image) -> Image.Image:
        """Write the crop and variants for one frame; return the cropped image."""
        box = self.box
        size = (self.output_width, self.output_height)
        if image.size != (self.frame_width, self.frame_height):
            self.frame_size_mismatch_count += 1
            box = self.config.region.resolve(*image.size)
            size = self.config.output_size(box)
            self.logger.warning(
                "Frame %d is %dx%d, expected %dx%d; cropping with a per-frame region",
                frame_index,
                image.width,
                image.height,
                self.frame_width,
                self.frame_height,
            )
        crop = image.crop((box.left, box.top, box.right, box.bottom))
        if self.config.scale != 1.0:
            crop = crop.resize(size, Image.BICUBIC)
        color_path = self._crops_dir / f"{frame_index:06d}.png"
        crop.save(color_path, compress_level=1)
        if self.config.variants:
            self._save_variants(crop, frame_index)
        self._sample_for_preview(frame_index, image, crop)
        self.crop_count += 1
        return crop

    def _save_variants(self, crop: Image.Image, frame_index: int) -> None:
        gray = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2GRAY)
        for variant in self.config.variants:
            if variant == "gray":
                data = gray
            elif variant == "clahe":
                data = self._clahe.apply(gray)
            elif variant == "otsu":
                _, data = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            else:
                data = cv2.adaptiveThreshold(
                    gray,
                    255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    self.config.adaptive_block_size,
                    self.config.adaptive_c,
                )
            Image.fromarray(data).save(self._crops_dir / variant / f"{frame_index:06d}.png", compress_level=1)

    def _sample_for_preview(self, frame_index: int, image: Image.Image, crop: Image.Image) -> None:
        samples = self._preview_samples
        seen = self.crop_count + 1
        if len(samples) < PREVIEW_SAMPLE_COUNT:
            samples.append((frame_index, image.copy(), crop.copy()))
        elif self._reservoir.random() * seen < PREVIEW_SAMPLE_COUNT:
            samples[self._reservoir.randrange(PREVIEW_SAMPLE_COUNT)] = (frame_index, image.copy(), crop.copy())

    @property
    def crops_dir(self) -> Path:
        return self._crops_dir

    def summary(self) -> dict[str, Any]:
        return {
            "region": self.config.region.as_dict(),
            "resolved_pixels": self.box.as_dict(),
            "scale": self.config.scale,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "variants": list(self.config.variants),
            "crop_count": self.crop_count,
            "frame_size_mismatch_count": self.frame_size_mismatch_count,
            "preview": "crop_preview.jpg",
        }

    def config_dict(self, status: str) -> dict[str, Any]:
        summary = self.summary()
        return {
            "stage": "stage1",
            "status": status,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "crop_width": self.box.width,
            "crop_height": self.box.height,
            **summary,
            "path_pattern": "crops/{frame_index:06d}.png",
            "variant_path_pattern": "crops/{variant}/{frame_index:06d}.png",
            "preview_samples": sorted(frame_index for frame_index, _, _ in self._preview_samples),
        }

    def close(self, status: str) -> None:
        self._write_config(status)
        self._write_preview()

    def _write_config(self, status: str) -> None:
        path = self.output_dir / "crop_config.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.config_dict(status), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)

    def _write_preview(self) -> None:
        samples = sorted(self._preview_samples, key=lambda sample: sample[0])
        if not samples:
            return
        panel_width = min(960, self.frame_width)
        margin = 8
        header_height = 20
        canvas_width = panel_width + margin * 2
        panels: list[Image.Image] = []
        for frame_index, full, crop in samples:
            ratio = panel_width / full.width
            full_small = full.resize((panel_width, max(1, _round_edge(full.height * ratio))), Image.BICUBIC)
            draw = ImageDraw.Draw(full_small)
            box = (
                self.box
                if full.size == (self.frame_width, self.frame_height)
                else self.config.region.resolve(*full.size)
            )
            draw.rectangle(
                (
                    _round_edge(box.left * ratio),
                    _round_edge(box.top * ratio),
                    _round_edge(box.right * ratio),
                    _round_edge(box.bottom * ratio),
                ),
                outline=(255, 48, 48),
                width=2,
            )
            draw.text((6, 4), f"frame {frame_index:06d}", fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
            crop_small = crop.resize((panel_width, max(1, _round_edge(crop.height * panel_width / crop.width))))
            panel = Image.new("RGB", (panel_width, header_height + full_small.height + crop_small.height), (24, 24, 24))
            panel.paste(full_small, (0, header_height))
            panel.paste(crop_small, (0, header_height + full_small.height))
            panels.append(panel)
        canvas = Image.new(
            "RGB",
            (canvas_width, sum(panel.height for panel in panels) + 10 * (len(panels) + 1)),
            (12, 12, 12),
        )
        offset = 10
        for panel in panels:
            canvas.paste(panel, (margin, offset))
            offset += panel.height + 10
        canvas.save(self.output_dir / "crop_preview.jpg", "JPEG", quality=85)
