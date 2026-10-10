"""Manifest merge + human-label syncing: never overwrite confirmed labels."""

from pathlib import Path

from creator_agent.character_collection.manifest import (
    CandidateRecord,
    load_manifest,
    merge_extraction,
    save_manifest,
    sync_labels,
)


def rec(cid: str, **kw) -> CandidateRecord:
    base = dict(
        candidate_id=cid,
        episode_id="935_1",
        scene_id="V001",
        timestamp_ms=1000,
        image_path=f"inbox/935_1/{cid}.jpg",
        quality_status="passed",
    )
    base.update(kw)
    return CandidateRecord(**base)


CID1 = "935_1_V001_T001000_C01"
CID2 = "935_1_V002_T002000_C01"


class TestManifestRoundtrip:
    def test_save_load(self, tmp_path: Path):
        path = tmp_path / "m.jsonl"
        records = {CID1: rec(CID1), CID2: rec(CID2, quality_status="filtered_blur", filter_reason="x")}
        save_manifest(path, records)
        loaded = load_manifest(path)
        assert set(loaded) == {CID1, CID2}
        assert loaded[CID2].quality_status == "filtered_blur"
        assert loaded[CID2].filter_reason == "x"

    def test_load_missing_file(self, tmp_path: Path):
        assert load_manifest(tmp_path / "nope.jsonl") == {}

    def test_atomic_write_leaves_no_tmp(self, tmp_path: Path):
        path = tmp_path / "m.jsonl"
        save_manifest(path, {CID1: rec(CID1)})
        assert path.is_file()
        assert not (tmp_path / "m.jsonl.tmp").exists()


class TestMergeExtraction:
    def test_preserves_human_label_on_rerun(self):
        old = {CID1: rec(CID1, character_id="doraemon", review_status="reviewed")}
        new = [rec(CID1), rec(CID2)]
        merged, report = merge_extraction(old, new)
        assert merged[CID1].character_id == "doraemon"
        assert merged[CID1].review_status == "reviewed"
        assert merged[CID2].review_status == "unreviewed"
        assert report.preserved_reviewed == 1

    def test_reviewed_record_missing_from_new_extraction_is_kept(self):
        old = {CID1: rec(CID1, character_id="nobita", review_status="reviewed")}
        merged, report = merge_extraction(old, [rec(CID2)])
        assert CID1 in merged
        assert merged[CID1].character_id == "nobita"
        assert report.kept_missing == [CID1]

    def test_unreviewed_record_is_refreshed(self):
        old = {CID1: rec(CID1, quality_status="filtered_blur", image_path="")}
        merged, _ = merge_extraction(old, [rec(CID1)])
        assert merged[CID1].quality_status == "passed"
        assert merged[CID1].image_path != ""


def _library_with_files(tmp_path: Path, files: dict[str, list[str]]) -> Path:
    """files: char_dir -> [candidate filenames]"""
    lib = tmp_path / "character_library"
    for char, names in files.items():
        d = lib / "characters" / char
        d.mkdir(parents=True)
        for name in names:
            (d / name).write_bytes(b"\xff\xd8\xff")  # minimal jpg header
    return lib


class TestSyncLabels:
    def test_classify_updates_manifest(self, tmp_path: Path):
        lib = _library_with_files(tmp_path, {"doraemon": [f"{CID1}.jpg"]})
        records = {CID1: rec(CID1), CID2: rec(CID2)}
        records, report = sync_labels(lib, "935_1", records)
        assert records[CID1].character_id == "doraemon"
        assert records[CID1].review_status == "reviewed"
        assert records[CID2].review_status == "unreviewed"
        assert report.updated == 1

    def test_confirmed_label_not_overwritten_by_different_folder(self, tmp_path: Path):
        lib = _library_with_files(tmp_path, {"nobita": [f"{CID1}.jpg"]})
        records = {CID1: rec(CID1, character_id="doraemon", review_status="reviewed")}
        records, report = sync_labels(lib, "935_1", records)
        assert records[CID1].character_id == "doraemon"  # untouched
        assert len(report.conflicts) == 1

    def test_same_candidate_in_two_folders_is_conflict(self, tmp_path: Path):
        lib = _library_with_files(tmp_path, {"doraemon": [f"{CID1}.jpg"], "unknown": [f"{CID1}.jpg"]})
        records = {CID1: rec(CID1)}
        records, report = sync_labels(lib, "935_1", records)
        assert records[CID1].review_status == "unreviewed"  # not applied
        assert len(report.conflicts) == 1

    def test_unparseable_filename_warned(self, tmp_path: Path):
        lib = _library_with_files(tmp_path, {"doraemon": ["holiday_snapshot.jpg"]})
        records = {CID1: rec(CID1)}
        _, report = sync_labels(lib, "935_1", records)
        assert len(report.unknown_files) == 1
        assert report.updated == 0

    def test_other_episode_file_warned_not_applied(self, tmp_path: Path):
        other = "936_1_V001_T001000_C01"
        lib = _library_with_files(tmp_path, {"doraemon": [f"{other}.jpg"]})
        records = {CID1: rec(CID1)}
        _, report = sync_labels(lib, "935_1", records)
        assert len(report.other_episode) == 1
        assert report.updated == 0

    def test_reviewed_but_file_deleted_warns_and_keeps_label(self, tmp_path: Path):
        lib = _library_with_files(tmp_path, {})
        records = {CID1: rec(CID1, character_id="doraemon", review_status="reviewed")}
        records, report = sync_labels(lib, "935_1", records)
        assert records[CID1].character_id == "doraemon"
        assert len(report.missing_files) == 1
