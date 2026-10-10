"""Candidate ID format/parse: stable, traceable, ASCII-only."""

from creator_agent.character_collection.naming import format_candidate_id, parse_candidate_id


class TestFormat:
    def test_example_from_spec(self):
        assert format_candidate_id("935_1", "V003", 1240, 1) == "935_1_V003_T001240_C01"

    def test_zero_padding(self):
        assert format_candidate_id("935_1", "V012", 654321, 5) == "935_1_V012_T654321_C05"


class TestParse:
    def test_roundtrip(self):
        cid = parse_candidate_id("935_1_V003_T001240_C01")
        assert cid is not None
        assert cid.episode_id == "935_1"
        assert cid.scene_id == "V003"
        assert cid.timestamp_ms == 1240
        assert cid.ordinal == 1
        assert str(cid) == "935_1_V003_T001240_C01"
        assert cid.filename == "935_1_V003_T001240_C01.jpg"

    def test_episode_id_with_multiple_underscores(self):
        cid = parse_candidate_id("935_1_extra_V001_T000100_C02")
        assert cid is not None
        assert cid.episode_id == "935_1_extra"
        assert cid.scene_id == "V001"

    def test_rejects_garbage(self):
        assert parse_candidate_id("random_name") is None
        assert parse_candidate_id("935_1_V003_T001240") is None
        assert parse_candidate_id("935_1_X003_T001240_C01") is None
        assert parse_candidate_id("") is None
