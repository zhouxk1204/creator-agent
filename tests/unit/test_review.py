"""Unit tests for asr/review.py — review.json build, annotation migration,
and rerun-safe writing. No worker/subprocess involved: pure dicts."""

from __future__ import annotations

import json

from creator_agent.asr import review

DIAG = {
    "chunk": 12,
    "chunk_reason": "normal",
    "time_source": "word_timed",
    "align_fallback": None,
    "speech_cov": 0.92,
    "repeat_tail_cut": False,
}


def _segments():
    return [
        {"start": 10.0, "end": 12.0, "text": "おはよう", "speaker": "話者A", "diag": dict(DIAG)},
        {"start": 20.0, "end": 22.5, "text": "今日は暑いね", "diag": {**DIAG, "align_fallback": "OUT_OF_WINDOW"}},
        {"start": 30.0, "end": 31.0, "text": "うん", "diag": dict(DIAG)},
    ]


def _srt_dicts():
    # Display-polished times differ slightly from speech times.
    return [
        {"start": 9.9, "end": 12.1, "text": "おはよう"},
        {"start": 19.9, "end": 22.6, "text": "今日は暑いね"},
        {"start": 29.9, "end": 31.5, "text": "うん"},
    ]


def _build(**kw):
    args = {
        "source": "ep01.mp4",
        "duration_sec": 601.2,
        "model": "Qwen/Qwen3-ASR-1.7B-hf",
        "overrides": {"vad_threshold": 0.35},
        "stats": {"asr_empty": 2, "align_fallback_OUT_OF_WINDOW": 3},
        "segments": _segments(),
        "srt_dicts": _srt_dicts(),
        "dropped": [{"start": 5.0, "end": 6.0, "text": "バイバイ", "speech_cov": 0.1}],
        "uncovered": [[65.2, 68.9]],
    }
    args.update(kw)
    return review.build_review(**args)


class TestBuildReview:
    def test_cue_ids_match_srt_numbering(self):
        rv = _build()
        assert [c["id"] for c in rv["cues"]] == [1, 2, 3]

    def test_display_and_speech_times_both_present(self):
        cue = _build()["cues"][0]
        assert (cue["start"], cue["end"]) == (9.9, 12.1)  # srt display
        assert (cue["speech_start"], cue["speech_end"]) == (10.0, 12.0)  # real speech

    def test_diag_and_annotation_fields(self):
        cue = _build()["cues"][1]
        assert cue["diag"]["align_fallback"] == "OUT_OF_WINDOW"
        assert cue["diag"]["chunk"] == 12
        assert cue["question"] == "" and cue["expected"] == ""
        assert cue["speaker"] is None  # segment 2 has no speaker

    def test_empty_text_cues_skipped_like_srt(self):
        segs = _segments() + [{"start": 40.0, "end": 41.0, "text": "  ", "diag": dict(DIAG)}]
        srt = _srt_dicts() + [{"start": 40.0, "end": 41.0, "text": "  "}]
        rv = _build(segments=segs, srt_dicts=srt)
        assert len(rv["cues"]) == 3  # write_srt would skip it too; ids stay aligned

    def test_dropped_and_uncovered_have_question(self):
        rv = _build()
        assert rv["dropped"][0]["question"] == ""
        assert rv["dropped"][0]["speech_cov"] == 0.1
        assert rv["uncovered"] == [{"start": 65.2, "end": 68.9, "question": ""}]

    def test_meta(self):
        rv = _build()
        assert rv["meta"]["source"] == "ep01.mp4"
        assert rv["meta"]["overrides"] == {"vad_threshold": 0.35}
        assert rv["meta"]["summary"]["asr_empty"] == 2

    def test_missing_diag_defaults_boundary_none(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "あ"}]
        rv = _build(segments=segs, srt_dicts=[{"start": 1.0, "end": 2.0, "text": "あ"}])
        assert rv["cues"][0]["diag"] == {"boundary": None}


class TestMigration:
    def _annotated_old(self):
        old = _build()
        old["cues"][0]["question"] = "字幕比语音晚 0.4s"
        old["cues"][0]["expected"] = "おはよう！"
        old["dropped"][0]["question"] = "这句是真台词，被误删"
        old["uncovered"][0]["question"] = "这里有一句低语漏了"
        return old

    def test_questions_migrate_by_text_and_time(self):
        old = self._annotated_old()
        new = _build()  # same text/times
        new, moved, orphaned = review.migrate_annotations(old, new)
        assert (moved, orphaned) == (3, 0)
        assert new["cues"][0]["question"] == "字幕比语音晚 0.4s"
        assert new["cues"][0]["expected"] == "おはよう！"
        assert new["dropped"][0]["question"].startswith("这句是真台词")
        assert new["uncovered"][0]["question"].startswith("这里有")

    def test_time_drift_beyond_tolerance_orphans(self):
        old = self._annotated_old()
        new = _build()
        new["cues"][0]["start"] = 11.0  # 1.1s away from old 9.9 > 0.5s tol
        new, moved, orphaned = review.migrate_annotations(old, new)
        assert moved == 2 and orphaned == 1
        assert new["cues"][0]["question"] == ""
        orphans = new["meta"]["orphaned_questions"]
        assert orphans[0]["kind"] == "cue"
        assert orphans[0]["question"] == "字幕比语音晚 0.4s"

    def test_changed_text_orphans(self):
        old = self._annotated_old()
        new = _build()
        new["cues"][0]["text"] = "おはようございます"  # ASR recognized differently
        _, moved, orphaned = review.migrate_annotations(old, new)
        assert moved == 2 and orphaned == 1

    def test_one_new_cue_absorbs_at_most_one_annotation(self):
        old = _build()
        old["cues"][0]["question"] = "q1"
        old["cues"][1]["question"] = "q2"
        old["cues"][1]["text"] = old["cues"][0]["text"]  # both old cues look like new cue 1
        old["cues"][1]["start"] = old["cues"][0]["start"]
        new = _build()
        new, moved, orphaned = review.migrate_annotations(old, new)
        assert moved == 1 and orphaned == 1
        assert new["cues"][0]["question"] == "q1"

    def test_unannotated_old_is_noop(self):
        old, new = _build(), _build()
        new, moved, orphaned = review.migrate_annotations(old, new)
        assert (moved, orphaned) == (0, 0)
        assert "orphaned_questions" not in new["meta"]


class TestWriteReview:
    def test_fresh_write(self, tmp_path):
        p = tmp_path / "ep01.review.json"
        assert review.write_review(p, _build()) == "fresh"
        loaded = json.loads(p.read_text(encoding="utf-8"))
        assert len(loaded["cues"]) == 3

    def test_overwrite_when_no_annotations(self, tmp_path):
        p = tmp_path / "ep01.review.json"
        review.write_review(p, _build())
        assert review.write_review(p, _build()) == "overwritten"

    def test_annotations_survive_rerun(self, tmp_path):
        p = tmp_path / "ep01.review.json"
        first = _build()
        first["cues"][1]["question"] = "时间轴偏早"
        review.write_review(p, first)
        # Simulate a rerun: same content, regenerated file.
        status = review.write_review(p, _build())
        assert status == "migrated:1"
        assert json.loads(p.read_text(encoding="utf-8"))["cues"][1]["question"] == "时间轴偏早"

    def test_corrupt_old_file_backed_up(self, tmp_path):
        p = tmp_path / "ep01.review.json"
        p.write_text("{not json", encoding="utf-8")
        assert review.write_review(p, _build()) == "fresh_after_bak"
        assert (tmp_path / "ep01.review.bak.json").exists()
        assert json.loads(p.read_text(encoding="utf-8"))["cues"]


class TestHasAnnotations:
    def test_empty_is_false(self):
        assert review.has_annotations(_build()) is False

    def test_whitespace_only_is_false(self):
        rv = _build()
        rv["cues"][0]["question"] = "   "
        assert review.has_annotations(rv) is False

    def test_expected_alone_counts(self):
        rv = _build()
        rv["cues"][2]["expected"] = "正确台词"
        assert review.has_annotations(rv) is True
