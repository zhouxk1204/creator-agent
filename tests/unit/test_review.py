"""Unit tests for asr/review.py — review file build and timestamped,
never-overwrite writing. No worker/subprocess involved: pure dicts."""

from __future__ import annotations

import json
from datetime import datetime

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


class TestWriteReview:
    T1 = datetime(2026, 10, 15, 19, 30, 0)
    T2 = datetime(2026, 10, 15, 21, 5, 42)

    def test_timestamped_filename(self, tmp_path):
        p = review.write_review(tmp_path, "ep01", _build(), now=self.T1)
        assert p.name == "ep01.review.20261015-193000.json"
        loaded = json.loads(p.read_text(encoding="utf-8"))
        assert len(loaded["cues"]) == 3

    def test_rerun_keeps_previous_file(self, tmp_path):
        first = _build()
        first["cues"][1]["question"] = "时间轴偏早"  # hand-filled annotation
        p1 = review.write_review(tmp_path, "ep01", first, now=self.T1)
        p2 = review.write_review(tmp_path, "ep01", _build(), now=self.T2)
        assert p1 != p2 and p1.exists() and p2.exists()
        # The old file keeps its annotation, untouched by the rerun.
        old = json.loads(p1.read_text(encoding="utf-8"))
        assert old["cues"][1]["question"] == "时间轴偏早"
        new = json.loads(p2.read_text(encoding="utf-8"))
        assert new["cues"][1]["question"] == ""

    def test_same_second_collision_gets_suffix(self, tmp_path):
        p1 = review.write_review(tmp_path, "ep01", _build(), now=self.T1)
        p2 = review.write_review(tmp_path, "ep01", _build(), now=self.T1)
        p3 = review.write_review(tmp_path, "ep01", _build(), now=self.T1)
        assert p1.name == "ep01.review.20261015-193000.json"
        assert p2.name == "ep01.review.20261015-193000-2.json"
        assert p3.name == "ep01.review.20261015-193000-3.json"
