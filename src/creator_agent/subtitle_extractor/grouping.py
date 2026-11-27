"""Stage 3: merge consecutive subtitle frames into subtitle events."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from creator_agent.subtitle_extractor.detection import mask_iou


class GroupingError(Exception):
    """Grouping was configured with unusable parameters."""


@dataclass(frozen=True)
class GroupingConfig:
    similarity_threshold: float = 0.85
    max_missing_frames: int = 2
    min_group_frames: int = 1
    candidate_frames: int = 3
    keep_frames: int = 9

    def __post_init__(self) -> None:
        if not math.isfinite(self.similarity_threshold) or not 0.0 < self.similarity_threshold <= 1.0:
            raise GroupingError("similarity threshold must be in (0, 1]")
        if self.max_missing_frames < 0:
            raise GroupingError("max missing frames cannot be negative")
        if self.min_group_frames < 1:
            raise GroupingError("min group frames must be at least 1")
        if self.candidate_frames < 1:
            raise GroupingError("candidate frames must be at least 1")
        if self.keep_frames < self.candidate_frames:
            raise GroupingError("keep frames must be at least the candidate frame count")


@dataclass
class SubtitleGroup:
    start_frame: int
    end_frame: int
    frame_count: int
    missing_frames: int
    best_frames: list[tuple[int, Image.Image, float]] = field(default_factory=list)

    @property
    def selected_frames(self) -> list[int]:
        return sorted(index for index, _, _ in self.best_frames)

    def ocr_frames(self, count: int) -> list[tuple[int, Image.Image, float]]:
        """Pick frames spread over the whole group, not just its sharpest frames.

        One caption can stay on screen for several seconds while the background
        moves; reading only the sharpest frames can miss the middle and end of
        the line. Evenly spaced picks keep the vote representative.
        """
        ordered = sorted(self.best_frames, key=lambda entry: entry[0])
        if len(ordered) <= count:
            return ordered
        if count == 1:
            middle = len(ordered) // 2
            return [ordered[middle]]
        step = (len(ordered) - 1) / (count - 1)
        picked = {int(round(step * position)) for position in range(count)}
        return [ordered[position] for position in sorted(picked)]


class GroupTracker:
    """Continuous state machine; one open group at a time.

    A frame with text that matches the previous accepted mask extends the group.
    A different mask closes the group and opens a new one. Up to
    ``max_missing_frames`` frames without text are tolerated before closing.
    """

    def __init__(self, config: GroupingConfig, on_close: Callable[[SubtitleGroup], None]) -> None:
        self.config = config
        self.on_close = on_close
        self.last_mask: np.ndarray | None = None
        self.last_index: int | None = None
        self._start: int | None = None
        self._end: int | None = None
        self._count = 0
        self._missing = 0
        self._best: list[tuple[int, Image.Image, float]] = []

    def add(self, frame_index: int, mask: np.ndarray, image: Image.Image, sharpness: float) -> float | None:
        similarity = None if self.last_mask is None else mask_iou(mask, self.last_mask)
        if self.last_mask is not None and similarity is not None:
            if similarity < self.config.similarity_threshold:
                self.close()
        if self._start is None:
            self._start = frame_index
            self._count = 0
            self._missing = 0
            self._best = []
        self._end = frame_index
        self._count += 1
        self._missing = 0
        self.last_mask = mask
        self.last_index = frame_index
        self._retain(frame_index, image, sharpness)
        return similarity

    def _retain(self, frame_index: int, image: Image.Image, sharpness: float) -> None:
        """Keep a spread of frames, capped at ``keep_frames``.

        Dropping the sharpness losers outright would leave only a burst of
        neighbouring frames from the moment the caption appeared, so new frames
        replace the sharper of the two closest neighbours instead.
        """
        entry = (frame_index, image.copy(), sharpness)
        self._best.append(entry)
        self._best.sort(key=lambda item: item[0])
        while len(self._best) > self.config.keep_frames:
            best_neighbour = min(
                range(len(self._best) - 1),
                key=lambda position: self._best[position + 1][0] - self._best[position][0],
            )
            left, right = self._best[best_neighbour], self._best[best_neighbour + 1]
            drop = best_neighbour if left[2] <= right[2] else best_neighbour + 1
            self._best.pop(drop)

    def skip(self, frame_index: int) -> None:
        if self._start is None:
            return
        self._missing += 1
        if self._missing > self.config.max_missing_frames:
            self.close()

    def close(self) -> None:
        if self._start is None or self._end is None:
            self._reset()
            return
        group = SubtitleGroup(
            start_frame=self._start,
            end_frame=self._end,
            frame_count=self._count,
            missing_frames=self._missing,
            best_frames=sorted(self._best, key=lambda item: item[0]),
        )
        self._reset()
        if group.frame_count >= self.config.min_group_frames:
            self.on_close(group)

    def finish(self) -> None:
        self.close()

    def _reset(self) -> None:
        self._start = None
        self._end = None
        self._count = 0
        self._missing = 0
        self._best = []
        self.last_mask = None
        self.last_index = None
