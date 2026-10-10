"""End-to-end on synthetic data: scenes.json + story video -> candidates.

Builds a tiny synthetic episode (sharp varied scene / black scene / static
scene), runs the full extract -> manifest -> contact-sheet -> sync flow, and
verifies traceability and rerun safety. No real anime footage needed.
"""

import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from creator_agent.character_collection.collector import extract_episode, inbox_dir
from creator_agent.character_collection.contact_sheet import build_contact_sheets
from creator_agent.character_collection.manifest import (
    CandidateRecord,
    load_manifest,
    manifest_path,
    save_manifest,
    sync_labels,
)
from creator_agent.character_collection.scene_reader import SceneReaderError, load_episode_scenes

FPS = 24
W, H = 320, 180
rng = np.random.default_rng(7)


def _noise():
    """Blocky texture: survives downscaling in the frame signature, sharp edges."""
    small = rng.integers(40, 220, size=(H // 4, W // 4, 3), dtype=np.uint8)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)


def _make_video(path: Path) -> None:
    """6s @24fps: 0-2s varied sharp | 2-3s black | 3-6s one static sharp frame."""
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    assert vw.isOpened(), "VideoWriter unavailable in this environment"
    static = _noise()
    for f in range(6 * FPS):
        t = f / FPS
        if t < 2.0:
            vw.write(_noise())
        elif t < 3.0:
            vw.write(np.zeros((H, W, 3), np.uint8))
        else:
            vw.write(static)
    vw.release()


def _make_episode(tmp_path: Path) -> tuple[Path, Path]:
    """Returns (scenes_json, video_path) in split_scene.py's output layout."""
    out = tmp_path / "output"
    scenes_dir = out / "935_1_scenes"
    scenes_dir.mkdir(parents=True)
    video = out / "935_1.mp4"
    _make_video(video)
    doc = {
        "source": "935_1.mp4",
        "detector": {"name": "ContentDetector", "threshold": 27.0, "min_scene_len": 15},
        "fps": float(FPS),
        "duration": 6.0,
        "scenes": [
            {
                "id": "V001",
                "index": 1,
                "start": 0.0,
                "end": 2.0,
                "duration": 2.0,
                "start_frame": 0,
                "end_frame": 48,
                "fps": float(FPS),
                "file": "001.mp4",
            },
            {
                "id": "V002",
                "index": 2,
                "start": 2.0,
                "end": 3.0,
                "duration": 1.0,
                "start_frame": 48,
                "end_frame": 72,
                "fps": float(FPS),
                "file": "002.mp4",
            },
            {
                "id": "V003",
                "index": 3,
                "start": 3.0,
                "end": 6.0,
                "duration": 3.0,
                "start_frame": 72,
                "end_frame": 144,
                "fps": float(FPS),
                "file": "003.mp4",
            },
        ],
    }
    scenes_json = scenes_dir / "scenes.json"
    scenes_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return scenes_json, video


@pytest.fixture()
def episode(tmp_path: Path):
    scenes_json, video = _make_episode(tmp_path)
    ep = load_episode_scenes(scenes_json)
    return ep, tmp_path / "character_library"


class TestSceneReader:
    def test_load(self, episode):
        ep, _ = episode
        assert ep.episode_id == "935_1"
        assert ep.video_path.name == "935_1.mp4"
        assert [s.scene_id for s in ep.scenes] == ["V001", "V002", "V003"]
        assert ep.scenes[1].start == 2.0 and ep.scenes[1].end == 3.0

    def test_missing_video_raises(self, tmp_path: Path):
        scenes_json, video = _make_episode(tmp_path)
        video.unlink()
        with pytest.raises(SceneReaderError, match="找不到故事视频"):
            load_episode_scenes(scenes_json)

    def test_video_override(self, tmp_path: Path):
        scenes_json, video = _make_episode(tmp_path)
        moved = tmp_path / "elsewhere.mp4"
        video.rename(moved)
        ep = load_episode_scenes(scenes_json, video_override=moved)
        assert ep.video_path == moved


class TestExtractEpisode:
    def test_extract_filters_and_traceability(self, episode):
        ep, library = episode
        records, stats = extract_episode(ep, library)

        assert stats.scenes_total == 3
        assert stats.planned == 3 + 1 + 3  # 2s->3, 1s->1, 3s->3
        # black scene fully filtered and recorded, not dropped
        assert "V002" in stats.fully_filtered_scenes

        v001 = [r for r in records.values() if r.scene_id == "V001"]
        v002 = [r for r in records.values() if r.scene_id == "V002"]
        v003 = [r for r in records.values() if r.scene_id == "V003"]
        assert all(r.quality_status == "passed" for r in v001)
        assert all(r.quality_status == "filtered_black" for r in v002)
        assert sum(r.quality_status == "passed" for r in v003) == 1
        assert sum(r.quality_status == "filtered_duplicate" for r in v003) == 2

        # traceability: every passed candidate has episode/scene/timestamp + real file
        for r in records.values():
            assert r.episode_id == "935_1"
            assert r.candidate_id.startswith(f"935_1_{r.scene_id}_T")
            assert r.scene_id in {"V001", "V002", "V003"}
            if r.quality_status == "passed":
                img = library / r.image_path
                assert img.is_file(), f"missing inbox image for {r.candidate_id}"
                assert img.stem == r.candidate_id
                # timestamp inside its scene (story-video relative)
                scene = next(s for s in ep.scenes if s.scene_id == r.scene_id)
                assert scene.start * 1000 <= r.timestamp_ms <= scene.end * 1000
            else:
                assert r.image_path == ""
                assert r.filter_reason != ""

        # only passed frames land in inbox
        inbox = inbox_dir(library, "935_1")
        assert len(list(inbox.glob("*.jpg"))) == stats.passed

        # manifest written and loadable
        mpath = manifest_path(library, "935_1")
        assert mpath.is_file()
        assert len(load_manifest(mpath)) == stats.planned

    def test_contact_sheets_written(self, episode):
        ep, library = episode
        records, _ = extract_episode(ep, library)
        sheets = build_contact_sheets(list(records.values()), library, ep.episode_id)
        assert len(sheets) == 1
        assert sheets[0].is_file()
        assert sheets[0].parent == library / "previews" / "935_1"

    def test_rerun_preserves_human_labels(self, episode):
        ep, library = episode
        records, _ = extract_episode(ep, library)

        # human classifies one candidate (copy into characters/doraemon/)
        passed = next(r for r in records.values() if r.quality_status == "passed")
        char_dir = library / "characters" / "doraemon"
        char_dir.mkdir(parents=True)
        shutil.copy(library / passed.image_path, char_dir / f"{passed.candidate_id}.jpg")
        records, report = sync_labels(library, "935_1", records)
        assert report.updated == 1
        save_manifest(manifest_path(library, "935_1"), records)

        # re-extraction: label survives, nothing silently lost
        records2, _ = extract_episode(ep, library)
        r2 = records2[passed.candidate_id]
        assert r2.character_id == "doraemon"
        assert r2.review_status == "reviewed"


class TestContactSheetPaging:
    def test_pages_and_stale_cleanup(self, tmp_path: Path):
        library = tmp_path / "character_library"
        inbox = library / "inbox" / "935_1"
        inbox.mkdir(parents=True)

        def fake_records(n: int) -> list[CandidateRecord]:
            out = []
            for i in range(n):
                cid = f"935_1_V{i + 1:03d}_T{i * 1000:06d}_C01"
                img = inbox / f"{cid}.jpg"
                cv2.imwrite(str(img), _noise())
                out.append(
                    CandidateRecord(
                        candidate_id=cid,
                        episode_id="935_1",
                        scene_id=f"V{i + 1:03d}",
                        timestamp_ms=i * 1000,
                        image_path=f"inbox/935_1/{cid}.jpg",
                        quality_status="passed",
                    )
                )
            return out

        sheets = build_contact_sheets(fake_records(25), library, "935_1")
        assert len(sheets) == 2  # 20 + 5

        sheets = build_contact_sheets(fake_records(5), library, "935_1")
        assert len(sheets) == 1
        leftover = list((library / "previews" / "935_1").glob("contact_sheet_*.jpg"))
        assert len(leftover) == 1  # stale page 2 removed
