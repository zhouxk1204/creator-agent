from pathlib import Path
from unittest.mock import MagicMock, call, patch

import cv2
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


def _end_refs():
    ref = np.full(split_episode.SIG_SIZE[::-1], 200, np.uint8)
    return [("end1.jpg", ref), ("end2.jpg", ref)]


def test_reference_card_beats_the_black_frame():
    """The card that follows the story marks its real end, so its time decides
    the cut even when a black run exists: black can also be a short flash INSIDE
    the story (an 11:14 flash in a story that really ends at 11:49)."""
    episodes = _episodes()
    cap = MagicMock()
    hits = [
        split_episode.EndRefResult(hit_frame=7325, hit_sim=0.9884),
        split_episode.EndRefResult(hit_frame=13699, hit_sim=0.9851),
    ]

    with (
        patch.object(split_episode, "open_capture", return_value=cap),
        patch.object(split_episode, "find_black_run_end") as find_black,
        patch.object(split_episode, "find_end_ref_frame", side_effect=hits) as find_ref,
    ):
        split_episode.adjust_episode_ends(Path("938.mp4"), 10.0, episodes, end_refs=_end_refs())

    # story #1 owns end1.jpg, story #2 end2.jpg; the scan is the story tail
    # (never the whole 8~12 minute body) and ends at the plain boundary.
    assert find_ref.call_args_list[0][0][2:4] == (759.0 - split_episode.END_REF_SEARCH_BACK, 759.0)
    assert find_ref.call_args_list[1][0][2:4] == (1500.0 - split_episode.END_REF_SEARCH_BACK, 1500.0)
    find_black.assert_not_called()
    # the cut goes ON the card's first frame, so the frame before it (the story's
    # real last picture) ends the episode; the similarity is recorded with it
    assert episodes[0]["end"] == 732.5
    assert episodes[0]["end_frame"] == 7325
    assert episodes[0]["end_source"] == "ref"
    assert episodes[0]["end_ref"] == "end1.jpg"
    assert episodes[0]["end_sim"] == 0.9884
    assert episodes[1]["end"] == 1369.9
    assert episodes[1]["end_ref"] == "end2.jpg"


def test_black_frame_is_the_fallback_when_the_reference_misses():
    episodes = _episodes()
    cap = MagicMock()
    miss = split_episode.EndRefResult(best_frame=9000, best_sim=0.94)

    with (
        patch.object(split_episode, "open_capture", return_value=cap),
        patch.object(split_episode, "find_black_run_end", side_effect=[6300, 12000]),
        patch.object(split_episode, "find_end_ref_frame", return_value=miss),
    ):
        split_episode.adjust_episode_ends(Path("938.mp4"), 10.0, episodes, end_refs=_end_refs())

    assert episodes[0]["end"] == 630.0
    assert episodes[0]["end_frame"] == 6300
    assert episodes[0]["end_source"] == "black"
    assert episodes[1]["end_frame"] == 12000
    assert episodes[1]["end_source"] == "black"


def test_end_ref_can_be_disabled():
    episodes = _episodes()
    cap = MagicMock()

    with (
        patch.object(split_episode, "open_capture", return_value=cap),
        patch.object(split_episode, "find_black_run_end", return_value=None),
        patch.object(split_episode, "find_end_ref_frame") as find_ref,
    ):
        split_episode.adjust_episode_ends(Path("938.mp4"), 10.0, episodes, end_refs=None)

    find_ref.assert_not_called()
    assert episodes[0]["end"] == 759.0


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


def _card_frames(count, level=200):
    return [np.full((72, 128, 3), level, np.uint8) for _ in range(count)]


def test_end_ref_returns_the_first_frame_of_the_hand_over_card():
    card = _card_frames(120)
    frames = _frames([(60, "content")]) + card + _frames([(60, "content")])
    ref = split_episode._frame_sig(card[0])
    fps = 30.0
    assert split_episode.find_end_ref_frame(_FakeCap(frames), fps, 0.0, len(frames) / fps - 0.001, ref).hit_frame == 60


def test_end_ref_reports_the_window_peak_when_it_misses():
    """A near miss (0.95 against a 0.96 threshold) must be reportable, so the
    log can tell it apart from "this card is nowhere in the window"."""
    content = _frames([(120, "content")])
    ref = split_episode._frame_sig(_card_frames(1)[0])
    fps = 30.0
    found = split_episode.find_end_ref_frame(_FakeCap(content), fps, 0.0, len(content) / fps - 0.001, ref)
    assert found.hit_frame is None
    assert found.best_frame is not None
    assert found.best_sim < split_episode.END_REF_MIN_SIM


def test_end_ref_ignores_a_single_matching_frame():
    """A lone frame that happens to look like the card must not become an end."""
    card = _card_frames(1)
    frames = _frames([(60, "content")]) + card + _frames([(60, "content")])
    ref = split_episode._frame_sig(card[0])
    fps = 30.0
    found = split_episode.find_end_ref_frame(_FakeCap(frames), fps, 0.0, len(frames) / fps - 0.001, ref)
    assert found.hit_frame is None


def test_end_ref_resumes_directly_after_a_rejected_match():
    """Rejecting a false hit must not shift the scan off by the frames it read
    while probing the hold, or the real card lands on the wrong index."""
    card = _card_frames(120)
    frames = _frames([(60, "content")]) + _card_frames(1) + _frames([(20, "content")]) + card
    ref = split_episode._frame_sig(card[0])
    fps = 30.0
    assert split_episode.find_end_ref_frame(_FakeCap(frames), fps, 0.0, len(frames) / fps - 0.001, ref).hit_frame == 81


def test_end_ref_respects_the_window():
    card = _card_frames(120)
    frames = _frames([(60, "content")]) + card
    ref = split_episode._frame_sig(card[0])
    fps = 30.0
    # window ends before the card starts -> nothing to find
    assert split_episode.find_end_ref_frame(_FakeCap(frames), fps, 0.0, 20 / fps, ref).hit_frame is None
    # same frames, full window -> the card's first frame
    assert split_episode.find_end_ref_frame(_FakeCap(frames), fps, 0.0, len(frames) / fps - 0.001, ref).hit_frame == 60


def test_load_end_refs_maps_end1_to_the_first_story(tmp_path):
    gray = np.full((72, 128), 200, np.uint8)
    for name in ("end1.jpg", "end2.jpg", "notes.txt"):
        path = tmp_path / name
        if name.endswith(".jpg"):
            ok, encoded = cv2.imencode(".jpg", gray)
            assert ok
            path.write_bytes(encoded.tobytes())
        else:
            path.write_text("not a reference", encoding="utf-8")

    refs = split_episode.load_end_refs(tmp_path)

    assert [name for name, _sig in refs] == ["end1.jpg", "end2.jpg"]
    assert all(sig.shape == split_episode.SIG_SIZE[::-1] for _name, sig in refs)


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
