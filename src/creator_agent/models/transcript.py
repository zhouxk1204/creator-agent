from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TranscriptSegment(BaseModel):
    """One ASR segment with its time range (seconds)."""

    start: float
    end: float
    text: str


class Transcript(BaseModel):
    """ASR transcript for a video, written to ``transcript.json``."""

    video_id: str
    text: str  # full transcript, segments joined
    segments: list[TranscriptSegment] = []
    language: str = "zh"
    model: str = ""
    duration_sec: float | None = None
    created_at: datetime
