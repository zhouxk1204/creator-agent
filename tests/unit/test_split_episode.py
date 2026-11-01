from pathlib import Path
from unittest.mock import MagicMock, call, patch

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
