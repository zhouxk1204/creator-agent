"""scene_reader: accept both scenes.json formats.

v1.0 (split_scene.py):  "source": "935_1.mp4", top-level "fps", scene "id": "V003"
v1.1 (other splitters): "source": {"file": "test.mp4", "fps": 30.0, ...},
                        scene "scene_id": "0003"
"""

import json
from pathlib import Path

import pytest

from creator_agent.character_collection.naming import parse_candidate_id
from creator_agent.character_collection.scene_reader import (
    SceneReaderError,
    load_episode_scenes,
)


def _write_layout(tmp_path: Path, doc: dict, video_name: str = "test.mp4") -> Path:
    """Mirror the real layout: <tmp>/test/scenes.json with the story video as
    the sibling <tmp>/<video_name> (scenes_dir.parent / source)."""
    work = tmp_path / "test"
    work.mkdir(parents=True)
    (tmp_path / video_name).write_bytes(b"fake")
    scenes_json = work / "scenes.json"
    scenes_json.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return scenes_json


V11_DOC = {
    "version": "1.1",
    "episode": "test",
    "source": {"file": "test.mp4", "duration": 605.9, "fps": 30.0},
    "scenes": [
        {"scene_id": "0001", "start": 0.0, "end": 1.967, "duration": 1.967},
        {"scene_id": "0002", "start": 1.967, "end": 5.433, "duration": 3.467},
    ],
}


class TestV11Format:
    def test_dict_source_resolves_video_and_fps(self, tmp_path: Path):
        scenes_json = _write_layout(tmp_path, V11_DOC)
        ep = load_episode_scenes(scenes_json)
        assert ep.episode_id == "test"
        assert ep.video_path.name == "test.mp4"
        assert ep.fps == 30.0
        assert [s.scene_id for s in ep.scenes] == ["0001", "0002"]
        assert all(s.fps == 30.0 for s in ep.scenes)  # inherited from source.fps

    def test_explicit_video_override(self, tmp_path: Path):
        scenes_json = _write_layout(tmp_path, V11_DOC)
        override = tmp_path / "elsewhere.mp4"
        override.write_bytes(b"fake")
        ep = load_episode_scenes(scenes_json, video_override=override)
        assert ep.video_path == override

    def test_missing_source_file_key_rejected(self, tmp_path: Path):
        doc = {**V11_DOC, "source": {"fps": 30.0}}
        scenes_json = _write_layout(tmp_path, doc)
        with pytest.raises(SceneReaderError, match="source"):
            load_episode_scenes(scenes_json)


class TestV10StillWorks:
    def test_string_source(self, tmp_path: Path):
        doc = {
            "source": "935_1.mp4",
            "fps": 24.0,
            "scenes": [{"id": "V001", "start": 0.0, "end": 2.0, "fps": 24.0}],
        }
        scenes_json = _write_layout(tmp_path, doc, video_name="935_1.mp4")
        ep = load_episode_scenes(scenes_json)
        assert ep.episode_id == "935_1"
        assert ep.fps == 24.0
        assert ep.scenes[0].scene_id == "V001"


class TestNumericSceneIds:
    """Bare numeric scene IDs must round-trip through candidate ID parsing."""

    @pytest.mark.parametrize("scene_id", ["V003", "0003", "V0003"])
    def test_parse(self, scene_id: str):
        cid = f"test_{scene_id}_T001240_C01"
        parsed = parse_candidate_id(cid)
        assert parsed is not None
        assert parsed.scene_id == scene_id
        assert parsed.episode_id == "test"
        assert parsed.timestamp_ms == 1240
        assert parsed.ordinal == 1
