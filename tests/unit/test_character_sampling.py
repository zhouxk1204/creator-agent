"""Sampling rules: candidate count by scene duration, uniform in-scene times."""

from creator_agent.character_collection.frame_sampler import planned_count, sample_times


class TestPlannedCount:
    def test_boundaries(self):
        assert planned_count(0.5) == 1
        assert planned_count(1.0) == 1
        assert planned_count(1.1) == 3
        assert planned_count(4.0) == 3
        assert planned_count(4.1) == 4
        assert planned_count(8.0) == 4
        assert planned_count(8.1) == 5
        assert planned_count(60.0) == 5


class TestSampleTimes:
    def test_count_matches_plan(self):
        for start, end, expected in [(0.0, 0.8, 1), (0.0, 2.0, 3), (0.0, 6.0, 4), (0.0, 12.0, 5)]:
            assert len(sample_times(start, end)) == expected

    def test_stays_inside_scene_with_margin(self):
        start, end = 10.0, 14.0
        times = sample_times(start, end)
        for t in times:
            assert start < t < end
        # edge margin: never right on the cut frames
        assert times[0] - start >= 0.1
        assert end - times[-1] >= 0.1

    def test_short_scene_uses_midpoint(self):
        assert sample_times(5.0, 5.5) == [5.25]

    def test_degenerate_scene(self):
        assert sample_times(3.0, 3.0) == [3.0]

    def test_uniform_spacing(self):
        times = sample_times(0.0, 8.0)
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert max(gaps) - min(gaps) < 1e-9
