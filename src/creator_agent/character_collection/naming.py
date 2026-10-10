"""Stable, traceable candidate IDs and filenames.

Format: ``{episode_id}_{scene_id}_T{timestamp_ms:06d}_C{ordinal:02d}``
e.g. ``935_1_V003_T001240_C01``.

The episode_id itself may contain underscores (``935_1``), so parsing always
works from the RIGHT side. IDs are pure ASCII so they render fine on contact
sheets with OpenCV's built-in fonts (no Japanese glyphs needed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Scene IDs are usually "V003" (split_scene.py) but other splitters use bare
# numerics ("0003"); both parse. Episode IDs may contain underscores, so the
# match is anchored from the right.
_RE_CANDIDATE = re.compile(r"^(?P<episode>.+)_(?P<scene>V?\d{3,})_T(?P<ms>\d{6,})_C(?P<idx>\d{2,})$")


@dataclass(frozen=True)
class CandidateId:
    episode_id: str
    scene_id: str
    timestamp_ms: int
    ordinal: int

    def __str__(self) -> str:
        return format_candidate_id(self.episode_id, self.scene_id, self.timestamp_ms, self.ordinal)

    @property
    def filename(self) -> str:
        return f"{self}.jpg"


def format_candidate_id(episode_id: str, scene_id: str, timestamp_ms: int, ordinal: int) -> str:
    return f"{episode_id}_{scene_id}_T{timestamp_ms:06d}_C{ordinal:02d}"


def parse_candidate_id(text: str) -> CandidateId | None:
    """Parse a candidate ID (or image filename stem). None if it doesn't match."""
    m = _RE_CANDIDATE.match(text)
    if not m:
        return None
    return CandidateId(
        episode_id=m.group("episode"),
        scene_id=m.group("scene"),
        timestamp_ms=int(m.group("ms")),
        ordinal=int(m.group("idx")),
    )
