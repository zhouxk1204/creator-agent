"""Stage 2: decide per frame whether the crop contains subtitle-like text.

The detector is deliberately heuristic and fully configurable: a top-hat
response isolates bright, thin strokes; connected components are filtered by
size/shape; components sharing a horizontal band and sitting close together
form a candidate text line, and the line is scored by brightness, stroke
density and component count. It never treats "bright pixel" alone as text.

Two details matter for real footage:

* a wide cluster is split at horizontal gaps, so a subtitle is not merged with
  unrelated bright background far to its right (that used to dilute density and
  hide whole lines);
* every candidate line is scored and the best one wins, instead of taking the
  widest cluster (which could be a bright background band).
"""

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


class DetectionError(Exception):
    """The crop cannot be analysed."""


@dataclass(frozen=True)
class DetectionConfig:
    threshold: float = 0.40
    top_hat_width: int = 15
    top_hat_height: int = 9
    bright_percentile: float = 97.0
    min_binary_value: int = 18
    min_height_ratio: float = 0.08
    max_height_ratio: float = 0.60
    min_width: int = 4
    max_width_ratio: float = 0.75
    min_area: int = 40
    min_fill: float = 0.08
    max_fill: float = 0.95
    max_aspect: float = 30.0
    band_tolerance_ratio: float = 0.08
    horizontal_gap_ratio: float = 1.5
    coverage_gate: float = 0.06
    small_coverage_gate: float = 0.035
    small_run_min_height_ratio: float = 0.20
    small_run_min_aspect: float = 0.9
    bright_level: int = 230
    bright_reference: float = 0.45
    density_reference: float = 0.30
    component_reference: float = 6.0
    bright_weight: float = 0.50
    density_weight: float = 0.30
    component_weight: float = 0.20

    def __post_init__(self) -> None:
        for name in ("threshold", "bright_reference", "density_reference", "component_reference"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise DetectionError(f"detection {name} must be finite and positive")
        weights = self.bright_weight + self.density_weight + self.component_weight
        if not math.isclose(weights, 1.0, abs_tol=1e-6):
            raise DetectionError("detection weights must sum to 1.0")
        if self.min_height_ratio <= 0 or self.max_height_ratio <= self.min_height_ratio:
            raise DetectionError("detection height ratios are invalid")
        if not math.isfinite(self.horizontal_gap_ratio) or self.horizontal_gap_ratio <= 0:
            raise DetectionError("detection horizontal gap ratio must be finite and positive")
        if not math.isfinite(self.coverage_gate) or not 0.0 <= self.coverage_gate <= 1.0:
            raise DetectionError("detection coverage gate must be within 0..1")
        if not math.isfinite(self.small_coverage_gate) or not 0.0 <= self.small_coverage_gate < self.coverage_gate:
            raise DetectionError("detection small coverage gate must be below the coverage gate")

    @property
    def enabled(self) -> bool:
        return True


@dataclass(frozen=True)
class FrameDetection:
    score: float
    has_subtitle: bool
    components: int
    coverage: float
    density: float
    bright_fraction: float
    bbox: tuple[int, int, int, int] | None
    mask: np.ndarray | None

    def as_row(self) -> dict[str, Any]:
        return {
            "has_subtitle": int(self.has_subtitle),
            "score": f"{self.score:.4f}",
            "components": self.components,
            "coverage": f"{self.coverage:.4f}",
            "density": f"{self.density:.4f}",
            "bright_fraction": f"{self.bright_fraction:.4f}",
            "text_bbox": " ".join(str(value) for value in self.bbox) if self.bbox else "",
        }


Component = tuple[int, int, int, int, float, int]  # left, top, width, height, centre y, label


def _band_clusters(components: list[Component], tolerance: float) -> list[list[Component]]:
    clusters: list[dict[str, Any]] = []
    for component in sorted(components, key=lambda item: item[4]):
        for cluster in clusters:
            if abs(component[4] - cluster["center"]) <= tolerance:
                cluster["items"].append(component)
                cluster["center"] = sum(item[4] for item in cluster["items"]) / len(cluster["items"])
                break
        else:
            clusters.append({"center": component[4], "items": [component]})
    return [cluster["items"] for cluster in clusters]


def _horizontal_runs(cluster: list[Component], gap_ratio: float) -> list[list[Component]]:
    """Split one band into runs whose glyphs actually touch each other."""
    items = sorted(cluster, key=lambda item: item[0])
    runs: list[list[Component]] = []
    current = [items[0]]
    for previous, nxt in zip(items, items[1:]):
        gap = nxt[0] - (previous[0] + previous[2])
        limit = gap_ratio * max(previous[3], nxt[3])
        if gap <= limit:
            current.append(nxt)
        else:
            runs.append(current)
            current = [nxt]
    runs.append(current)
    return runs


def _score_run(
    run: list[Component],
    *,
    labels: np.ndarray,
    gray: np.ndarray,
    width: int,
    config: DetectionConfig,
) -> tuple[float, FrameDetection] | None:
    left = min(item[0] for item in run)
    top = min(item[1] for item in run)
    right = max(item[0] + item[2] for item in run)
    bottom = max(item[1] + item[3] for item in run)
    if right <= left or bottom <= top:
        return None
    coverage = (right - left) / width
    if coverage < config.small_coverage_gate:
        return None
    if coverage < config.coverage_gate:
        # A single short caption ("哇") is only ~40 px wide, which used to fall
        # under the coverage gate. Let it through only when it looks like a tall
        # glyph rather than a flat patch of background.
        height = gray.shape[0]
        if (bottom - top) / height < config.small_run_min_height_ratio or (right - left) / (
            bottom - top
        ) < config.small_run_min_aspect:
            return None

    mask = np.zeros(gray.shape, dtype=np.uint8)
    for component in run:
        mask[labels == component[5]] = 255
    density = float((mask[top:bottom, left:right] > 0).mean())
    bright_fraction = float((gray[top:bottom, left:right] >= config.bright_level).mean())
    score = (
        config.bright_weight * min(1.0, bright_fraction / config.bright_reference)
        + config.density_weight * min(1.0, density / config.density_reference)
        + config.component_weight * min(1.0, len(run) / config.component_reference)
    )
    detection = FrameDetection(
        score=round(score, 4),
        has_subtitle=score >= config.threshold,
        components=len(run),
        coverage=round(coverage, 4),
        density=round(density, 4),
        bright_fraction=round(bright_fraction, 4),
        bbox=(left, top, right - left, bottom - top),
        mask=mask if score >= config.threshold else None,
    )
    return score, detection


def detect_subtitle(image: np.ndarray, config: DetectionConfig = DetectionConfig()) -> FrameDetection:
    """Return the subtitle score and the text mask for one crop.

    ``image`` is BGR (OpenCV order) and must be a single crop, not a full frame.
    """
    if image is None or image.size == 0:
        raise DetectionError("empty crop image")
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (config.top_hat_width, config.top_hat_height))
    top_hat = cv2.morphologyEx(blurred, cv2.MORPH_TOPHAT, kernel)
    threshold = max(config.min_binary_value, int(np.percentile(top_hat, config.bright_percentile)))
    _, binary = cv2.threshold(top_hat, threshold, 255, cv2.THRESH_BINARY)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    total, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    components: list[Component] = []
    for index in range(1, total):
        left, top, box_width, box_height, area = stats[index]
        if not (
            config.min_height_ratio * height <= box_height <= config.max_height_ratio * height
            and config.min_width <= box_width <= config.max_width_ratio * width
            and area >= config.min_area
            and config.min_fill <= area / (box_width * box_height) <= config.max_fill
            and box_width / box_height <= config.max_aspect
        ):
            continue
        components.append(
            (int(left), int(top), int(box_width), int(box_height), float(centroids[index][1]), int(index))
        )

    if not components:
        return FrameDetection(0.0, False, 0, 0.0, 0.0, 0.0, None, None)

    clusters = _band_clusters(components, tolerance=max(6.0, config.band_tolerance_ratio * height))
    best: FrameDetection | None = None
    best_score = 0.0
    for cluster in clusters:
        for run in _horizontal_runs(cluster, config.horizontal_gap_ratio):
            scored = _score_run(run, labels=labels, gray=gray, width=width, config=config)
            if scored is None:
                continue
            score, candidate = scored
            if best is None or score > best_score:
                best, best_score = candidate, score
    if best is None:
        return FrameDetection(0.0, False, 0, 0.0, 0.0, 0.0, None, None)
    if not best.has_subtitle:
        # Keep the geometric evidence for diagnostics but drop the mask so the
        # grouper never sees a candidate that failed the threshold.
        return FrameDetection(
            best.score,
            False,
            best.components,
            best.coverage,
            best.density,
            best.bright_fraction,
            best.bbox,
            None,
        )
    return best


def mask_iou(first: np.ndarray, second: np.ndarray) -> float:
    """Intersection-over-union of two binary text masks."""
    if first is None or second is None or first.shape != second.shape:
        return 0.0
    union = np.count_nonzero(np.logical_or(first > 0, second > 0))
    if union == 0:
        return 0.0
    return float(np.count_nonzero(np.logical_and(first > 0, second > 0)) / union)


def sharpness(image: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Laplacian variance inside the text mask bbox (higher means crisper strokes)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if mask is not None:
        ys, xs = np.nonzero(mask)
        if ys.size and xs.size:
            top, bottom = int(ys.min()), int(ys.max()) + 1
            left, right = int(xs.min()), int(xs.max()) + 1
            gray = gray[top:bottom, left:right]
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())
