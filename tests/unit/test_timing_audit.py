"""Stage-1 model-free regressions. Added but NOT RUN during implementation."""

from __future__ import annotations

import copy
import io
import json
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from creator_agent.asr import aligner_worker, ja_worker, timing_audit
from creator_agent.asr.ja_transcriber import JaTranscriber
from creator_agent.config import JaAsrSettings


def _audit(tmp_path):
    return {
        "run_id": "synthetic-run",
        "checkpoint_path": str(tmp_path / "timing.json"),
        "duration_sec": 5.0,
        "chunks": [
            {
                "idx": 0,
                "pass": "initial",
                "start": 0.9,
                "end": 2.4,
                "raw_text": "はいです",
                "alignment_text": "はいです",
                "asr_status": "ok",
                "raw_alignment": {"ok": True, "words": [["はい", 1.123456, 1.7], ["です", 1.7, 2.234567]]},
            }
        ],
    }


def test_inspection_uses_raw_words_not_gate_repairs(tmp_path):
    audit = _audit(tmp_path)
    audit["chunks"][0]["alignment_gate"] = {"ok": False, "reason": "LOW_COVERAGE"}
    audit["chunks"][0]["repaired_words"] = [["はい", 1.2, 1.8], ["です", 1.8, 2.3]]
    before = copy.deepcopy(audit)
    chunks, words, warnings = timing_audit.inspection_cues(audit)
    assert (chunks[0]["start"], chunks[0]["end"]) == (1.123456, 2.234567)
    assert (words[0]["start"], words[0]["end"]) == (1.123456, 1.7)
    assert warnings == []
    assert audit == before


def test_invalid_word_is_recorded_not_stretched(tmp_path):
    audit = _audit(tmp_path)
    audit["chunks"][0]["raw_alignment"]["words"].insert(1, ["は", 1.7, 1.7])
    before = copy.deepcopy(audit)
    chunks, words, warnings = timing_audit.inspection_cues(audit)
    assert len(chunks) == 1
    assert len(words) == 2
    assert any(w["reason"] == "UNRENDERABLE_RAW_WORD" and w["raw_word"] == ["は", 1.7, 1.7] for w in warnings)
    assert audit == before


def test_failed_alignment_is_not_replaced_with_proportional_inspection(tmp_path):
    audit = _audit(tmp_path)
    audit["chunks"][0]["raw_alignment"] = {"ok": False, "words": None, "error": "synthetic model error"}
    audit["chunks"][0]["repaired_words"] = [["はいです", 1.0, 2.0]]
    chunks, words, warnings = timing_audit.inspection_cues(audit)
    assert chunks == words == []
    assert warnings[0]["reason"] == "NO_RAW_ALIGNMENT"
    assert warnings[0]["detail"] == "synthetic model error"


def test_raw_overlap_is_retained_and_marked(tmp_path):
    audit = _audit(tmp_path)
    second = copy.deepcopy(audit["chunks"][0])
    second.update(idx=1, **{"pass": "recovered"})
    audit["chunks"].append(second)
    chunks, _, warnings = timing_audit.inspection_cues(audit)
    assert chunks[0]["start"] == chunks[1]["start"] == 1.123456
    assert any(w["reason"] == "RAW_OVERLAP" for w in warnings)


def test_checkpoint_preserves_precision_and_serializes_nonfinite_values(tmp_path):
    audit = _audit(tmp_path)
    audit["nonfinite_sample"] = float("nan")
    timing_audit.save_checkpoint(audit, "ALIGNMENT_PARTIAL")
    raw = (tmp_path / "timing.json").read_text(encoding="utf-8")
    saved = json.loads(raw)
    assert saved["chunks"][0]["raw_alignment"]["words"][0][1] == 1.123456
    assert saved["nonfinite_sample"] == {"nonfinite_float": "nan"}
    assert saved["stage"] == "ALIGNMENT_PARTIAL"
    assert '"nonfinite_float"' in raw


def test_failed_checkpoint_replace_keeps_prior_snapshot(tmp_path):
    audit = _audit(tmp_path)
    timing_audit.save_checkpoint(audit, "ASR_PARTIAL")
    original = (tmp_path / "timing.json").read_bytes()
    with mock.patch.object(timing_audit.os, "replace", side_effect=OSError("synthetic disk error")):
        with pytest.raises(OSError, match="synthetic disk error"):
            timing_audit.save_checkpoint(audit, "ALIGNMENT_PARTIAL")
    assert (tmp_path / "timing.json").read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_aligner_details_keep_local_and_global_values():
    local = [SimpleNamespace(text="はい", start_time=0.123456, end_time=0.8)]
    model = mock.Mock()
    model.align.return_value = [local]
    rec = aligner_worker._align_details(model, {"wav": "synthetic.wav", "text": "はい", "chunk_start": 9.7}, "Japanese")
    assert rec["local_words"] == [("はい", 0.123456, 0.8)]
    assert rec["words"][0][1:] == pytest.approx((9.823456, 10.5))
    assert rec["offset_sec"] == 9.7
    model.align.assert_called_once()


def test_alignment_window_uses_actual_slice_sample_offset(tmp_path):
    chunk = {"idx": 0, "start": 1.00013, "end": 2.0, "text": "はい"}
    audit = {"chunks": [{"idx": 0}]}
    args = SimpleNamespace(
        aligner_python=sys.executable, aligner_model="synthetic", align_pad=0.3, device="cpu", language="Japanese"
    )
    returned = {"chunk_id": 0, "ok": True, "words": [["はい", 1.2, 1.5]], "local_words": [["はい", 0.5, 0.8]]}
    proc = mock.Mock()
    proc.stdout = io.BytesIO(("===ALIGN_CHUNK=== " + json.dumps(returned) + "\n").encode())
    proc.stderr = io.BytesIO(b"")
    proc.wait.return_value = 0
    with (
        mock.patch.object(ja_worker, "_slice_wav", return_value=tmp_path / "synthetic.wav"),
        mock.patch.object(ja_worker.subprocess, "Popen", return_value=proc),
    ):
        results = ja_worker._align_stream(
            args, tmp_path, [chunk], "synthetic", tmp_path / "vocals.wav", 5.0, audit=audit
        )
    jobs = json.loads((tmp_path / "align_jobs.json").read_text(encoding="utf-8"))
    assert jobs[0]["chunk_start"] == int((1.00013 - 0.3) * 16000) / 16000
    assert jobs[0]["chunk_start"] == jobs[0]["window"]["start"]
    assert results[0] == returned
    assert audit["chunks"][0]["raw_alignment"] == returned


def test_inspection_and_final_outputs_are_separate_and_raw_json_is_exact(tmp_path):
    audit = _audit(tmp_path)
    result = {
        "text": "はいです",
        "duration_sec": 5.0,
        "timing_audit": audit,
        "segments": [{"start": 3.0, "end": 4.0, "text": "はいです", "diag": {"time_source": "proportional"}}],
    }
    transcriber = JaTranscriber(JaAsrSettings(boundary_check=False, review_json=True))
    transcriber._write_outputs(tmp_path / "synthetic.mp4", tmp_path, result)
    target = tmp_path / "synthetic"
    raw_srt = (target / "synthetic.alignment.synthetic-run.srt").read_text(encoding="utf-8")
    final_srt = (target / "synthetic.final.synthetic-run.srt").read_text(encoding="utf-8")
    assert "00:00:01,123 --> 00:00:02,235" in raw_srt
    assert "00:00:03,000 --> 00:00:04,000" in final_srt
    saved = json.loads((tmp_path / "timing.json").read_text(encoding="utf-8"))
    assert saved["chunks"][0]["raw_alignment"]["words"][0][1] == 1.123456
    assert saved["final_srt_cues"][0]["start"] == 3.0
    assert saved["stage"] == "OUTPUT_COMPLETE"
    assert (target / "synthetic.asr.raw.synthetic-run.txt").exists()
    review = json.loads(next(target.glob("*.review.*.json")).read_text(encoding="utf-8"))
    assert review["meta"]["run_id"] == "synthetic-run"
    assert "not raw" in review["meta"]["speech_fields_meaning"]


def test_aligner_load_failure_streams_structured_records(tmp_path, monkeypatch, capsys):
    jobs = [{"chunk_id": 0, "wav": "synthetic.wav", "text": "はい", "chunk_start": 1.0}]
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(jobs), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["aligner_worker.py", "--jobs", str(path), "--model", "synthetic"])
    with mock.patch.object(aligner_worker, "_load_aligner", side_effect=RuntimeError("synthetic load failure")):
        assert aligner_worker.main() == 0
    lines = capsys.readouterr().out.splitlines()
    rec = json.loads(
        next(line.removeprefix("===ALIGN_CHUNK=== ") for line in lines if line.startswith("===ALIGN_CHUNK=== "))
    )
    assert rec["ok"] is False
    assert "synthetic load failure" in rec["error"]
