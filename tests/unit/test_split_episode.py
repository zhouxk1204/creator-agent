from pathlib import Path
from unittest.mock import MagicMock, call, patch

import numpy as np

from scripts import split_episode


def _episodes():
    # The second story's title card starts at 00:12:39 (759 seconds).
    return [
        {"name": "938_1", "start": 60.0, "end": 759.0},
        {"name": "938_2", "start": 759.0, "end": 1500.0},
    ]


def test_first_story_end_is_at_least_two_minutes_before_next_card():
    episodes = _episodes()
    cap = MagicMock()

    with (
        patch.object(split_episode, "open_capture", return_value=cap),
        patch.object(split_episode, "find_black_run_end", side_effect=[6300, None]) as find_black,
    ):
        split_episode.adjust_episode_ends(Path("938.mp4"), 10.0, episodes)

    # For a 00:12:39 card, the search ends at 00:10:39. It starts at 00:09:00
    # (the earliest healthy 8-minute story end), so neither mid-story black
    # frames nor transitions at 00:11:00 or later can become story #1's end.
    assert find_black.call_args_list[0] == call(cap, 10.0, 540.0, 639.0)
    assert episodes[0]["end"] == 630.0  # 00:10:30
    assert episodes[0]["end_frame"] == 6300
    cap.release.assert_called_once_with()


def test_first_story_does_not_fall_back_to_black_near_next_card():
    episodes = _episodes()
    cap = MagicMock()

    with (
        patch.object(split_episode, "open_capture", return_value=cap),
        patch.object(split_episode, "find_black_run_end", return_value=None),
    ):
        split_episode.adjust_episode_ends(Path("938.mp4"), 10.0, episodes)

    assert episodes[0]["end"] == 759.0
    assert "end_frame" not in episodes[0]
    issues = split_episode.split_anomalies([object(), object()], episodes)
    assert any("尾帧数量异常" in issue for issue in issues)
    cap.release.assert_called_once_with()


def test_packed_stories_use_card_adjacent_fallback():
    """When nothing sits two minutes before the card, the story end is the last
    black run before it that is followed by content (not the card transition-in)."""
    episodes = _episodes()
    cap = MagicMock()

    with (
        patch.object(split_episode, "open_capture", return_value=cap),
        patch.object(split_episode, "find_black_run_end", side_effect=[None, 6900, 12000]) as find_black,
    ):
        split_episode.adjust_episode_ends(Path("938.mp4"), 10.0, episodes)

    assert find_black.call_args_list[1] == call(cap, 10.0, 540.0, 759.0, gap_after=1.0)
    assert episodes[0]["end"] == 690.0
    assert episodes[0]["end_frame"] == 6900


class _FakeCap:
    """Sequential reader over prebuilt frames (f0 must be 0)."""

    def __init__(self, frames):
        self.frames = frames
        self.pos = 0

    def set(self, _prop, value):
        self.pos = int(value)
        return True

    def read(self):
        if self.pos >= len(self.frames):
            return False, None
        frame = self.frames[self.pos]
        self.pos += 1
        return True, frame


def _frames(spec):
    """spec: list of (count, kind) with kind in {"content", "black", "gray"}."""
    out = []
    rng = np.random.default_rng(7)
    for count, kind in spec:
        for _ in range(count):
            if kind == "content":
                out.append(rng.integers(0, 255, (72, 128, 3), dtype=np.uint8))
            elif kind == "black":
                out.append(np.zeros((72, 128, 3), np.uint8))
            else:  # flat off-black solid, same as 39-luma gray gaps
                out.append(np.full((72, 128, 3), 39, np.uint8))
    return out


def test_off_black_flat_solid_counts_as_black():
    frames = _frames([(60, "content"), (12, "gray"), (90, "content")])
    cap = _FakeCap(frames)
    end = split_episode.find_black_run_end(cap, 30.0, 0.0, len(frames) / 30.0 - 0.001)
    assert end == 60


def test_gap_after_rejects_black_that_runs_into_the_next_card():
    frames = _frames([(60, "content"), (12, "gray"), (90, "content"), (45, "black")])
    fps = 30.0
    t1 = len(frames) / fps - 0.001
    # the trailing black touches the window edge (= the next card) -> rejected
    assert split_episode.find_black_run_end(_FakeCap(frames), fps, 0.0, t1, gap_after=1.0) == 60
    assert split_episode.find_black_run_end(_FakeCap(frames), fps, 0.0, t1) == 162


def test_title_line_survives_chaining_with_background_art():
    """Union-find chains title glyphs to art blobs of similar height; the line
    clustering must still recover the title baseline instead of dropping it."""
    glyph_h, glyph_y = 180, 60
    boxes = [(100 + 160 * i, glyph_y, 140, glyph_h) for i in range(8)]
    # art blobs of the same height, staggered down the frame so union-find
    # links them into one group together with the glyphs
    for k, y in enumerate((142, 224, 306, 388, 470), start=1):
        boxes.append((60 + 300 * k, y, 260, glyph_h))

    lines = split_episode._baseline_lines(boxes)
    h = 1080  # arbitrary frame height for normalization only
    title = [ln for ln in lines if abs(np.median([b[1] + b[3] / 2 for b in ln]) - (glyph_y + glyph_h / 2)) < 20]
    assert len(title) == 1
    assert len(title[0]) >= split_episode.TEXT_LINE_MIN
    assert np.median([b[3] for b in title[0]]) / h > 0  # sanity: heights carried through
