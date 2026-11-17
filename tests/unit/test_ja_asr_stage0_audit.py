"""Historical stage-0 characterizations plus stage-1 timing regressions.

Fixed cases assert the new contract; stored stage-0 output files are unchanged.
These tests are not subtitle-quality acceptance and never load models.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from dataclasses import asdict
from types import SimpleNamespace
from unittest import mock

import pytest

from creator_agent.asr import aligner_worker, boundary_check, ja_worker
from creator_agent.asr.audio_extractor import extract_audio
from creator_agent.asr.cue_splitter import split_cues
from creator_agent.asr.ja_transcriber import JaTranscriber
from creator_agent.asr.subtitle import finalize_for_srt, segments_to_srt
from creator_agent.config import JaAsrSettings
from creator_agent.models.transcript import TranscriptSegment


def _record(tmp_path, case_id, *, requirement, evidence, inputs, observed, requirement_met):
    result = {
        "case_id": case_id,
        "data_kind": "synthetic; no model inference or human audio reference",
        "requirement": requirement,
        "requirement_status": "MET" if requirement_met else "NOT_MET",
        "evidence": evidence,
        "inputs": inputs,
        "observed": observed,
    }
    (tmp_path / "case.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_stage1_boundary_preserves_word_timing_before_export(tmp_path):
    settings = JaAsrSettings(boundary_check=True, review_json=True)
    transcriber = JaTranscriber(settings)
    result = {
        "video_id": "synthetic",
        "ok": True,
        "text": "はい",
        "duration_sec": 5.0,
        "segments": [{"start": 1.35, "end": 2.0, "text": "はい", "diag": {"time_source": "word_timed"}}],
    }
    before = copy.deepcopy(result)
    video = tmp_path / "synthetic.mp4"
    with (
        mock.patch.object(boundary_check, "detect_silences", return_value=[(0.0, 1.24)]),
        mock.patch.object(boundary_check, "probe_video_duration", return_value=5.0),
    ):
        transcriber._check_boundaries([result], [(video, "synthetic")], tmp_path)
    transcriber._write_outputs(video, tmp_path, result)
    target = tmp_path / "synthetic"
    transcript = json.loads((target / "synthetic.transcript.json").read_text(encoding="utf-8"))
    review = json.loads(next(target.glob("*.review.*.json")).read_text(encoding="utf-8"))
    observed = {"after_boundary": result, "transcript": transcript, "review": review}
    _record(
        tmp_path,
        "S0-TIME-01",
        requirement="silencedetect 默认仅诊断，不覆盖 ForcedAligner 时间；原始对齐时间应可追溯。",
        evidence=[
            "src/creator_agent/asr/boundary_check.py:205-228 snap_edge",
            "src/creator_agent/asr/ja_transcriber.py:207-234 _check_boundaries",
            "src/creator_agent/asr/review.py:84-101 build_review",
        ],
        inputs={"worker_result": before, "silences": [[0.0, 1.24]]},
        observed=observed,
        requirement_met=result["segments"][0]["start"] == before["segments"][0]["start"],
    )
    assert result["segments"][0]["start"] == 1.35
    assert transcript["segments"][0]["start"] == 1.35
    assert review["cues"][0]["speech_start"] == 1.35
    assert review["cues"][0]["diag"]["boundary"] == "diagnostic_only"
    assert review["cues"][0]["diag"]["time_source"] == "word_timed"
    assert "flags" not in review["cues"][0]["diag"]
    assert "diag" not in transcript["segments"][0]


def test_stage1_boundary_min_duration_does_not_create_overlap(tmp_path):
    cues = [{"start": 9.5, "end": 9.9, "text": "はい"}, {"start": 9.95, "end": 10.0, "text": "うん"}]
    refined, flags = boundary_check.refine_cues(cues, [], audio_duration=10.0)
    overlap = refined[0]["end"] - refined[1]["start"]
    _record(
        tmp_path,
        "S0-TIME-02",
        requirement="边界检查不应为满足 min_dur 在媒体末尾制造字幕重叠。",
        evidence=["src/creator_agent/asr/boundary_check.py:262-270 refine_cues"],
        inputs={"cues": cues, "silences": [], "duration": 10.0},
        observed={"cues": refined, "flags": flags, "overlap_sec": overlap},
        requirement_met=overlap <= 0,
    )
    assert refined == cues
    assert overlap < 0
    assert flags[0]["reasons"] == [boundary_check.SHORT_CUE]


def test_stage0_optional_sidecars_can_describe_previous_run(tmp_path):
    transcriber = JaTranscriber(JaAsrSettings(review_json=True))
    video = tmp_path / "synthetic.mp4"
    first = {
        "text": "はい",
        "duration_sec": 5.0,
        "segments": [{"start": 1.0, "end": 2.0, "text": "はい"}],
        "dropped": [{"start": 3.0, "end": 4.0, "text": "うん", "speech_cov": 0.1}],
        "boundary_report": "synthetic first-run boundary report\n",
    }
    second = {**first, "dropped": [], "boundary_report": None}
    transcriber._write_outputs(video, tmp_path, first)
    transcriber._write_outputs(video, tmp_path, second)
    target = tmp_path / "synthetic"
    reviews = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(target.glob("*.review.*.json"))]
    observed = {
        "dropped_txt": (target / "synthetic.dropped.txt").read_text(encoding="utf-8"),
        "check_txt": (target / "synthetic.check.txt").read_text(encoding="utf-8"),
        "reviews": reviews,
    }
    _record(
        tmp_path,
        "S0-QC-01",
        requirement="同一输出目录重跑时，应标明 sidecar 所属运行，避免旧记录冒充当前结果；保留旧数据。",
        evidence=["src/creator_agent/asr/ja_transcriber.py:410-420 _write_outputs"],
        inputs={"first": first, "second": second},
        observed=observed,
        requirement_met=not observed["dropped_txt"] and not observed["check_txt"],
    )
    assert "うん" in observed["dropped_txt"]
    assert "first-run" in observed["check_txt"]
    assert len(reviews) == 2  # Existing history preservation must not be removed.
    assert sorted(len(r["dropped"]) for r in reviews) == [0, 1]


def test_stage0_aligner_offset_is_added_once(tmp_path):
    job = {"wav": "synthetic.wav", "text": "はい", "chunk_start": 9.7}
    local_words = [SimpleNamespace(text="はい", start_time=0.3, end_time=0.8)]
    aligner = mock.Mock()
    aligner.align.return_value = [local_words]
    words = aligner_worker._align_one(aligner, job, "Japanese")
    _record(
        tmp_path,
        "S0-TIME-03",
        requirement="局部词时间应加实际对齐窗口起点一次，不漏加、重复加或使用未带 pad 的 ASR 起点。",
        evidence=["src/creator_agent/asr/aligner_worker.py:80-90 _align_one"],
        inputs={"job": job, "local_words": [["はい", 0.3, 0.8]], "asr_chunk_start": 10.0},
        observed={
            "global_words": words,
            "local_words_after": [[w.text, w.start_time, w.end_time] for w in local_words],
        },
        requirement_met=words[0][1] == pytest.approx(10.0) and words[0][2] == pytest.approx(10.5),
    )
    assert words[0][1:] == pytest.approx((10.0, 10.5))
    assert (local_words[0].start_time, local_words[0].end_time) == (0.3, 0.8)
    aligner.align.assert_called_once_with(audio="synthetic.wav", text="はい", language="Japanese")


def test_stage0_gate_repairs_but_does_not_mutate_raw_record(tmp_path):
    raw = {"ok": True, "words": [["はい", 1.0, 1.5], ["そう", 1.5, 1.5], ["です", 1.6, 2.0]]}
    before = copy.deepcopy(raw)
    chunk = {"start": 0.9, "end": 2.1, "text": "はいそうです"}
    args = SimpleNamespace(align_pad=0.3, align_min_coverage=0.5)
    check, repaired = ja_worker._gate_alignment(raw, chunk, args)
    _record(
        tmp_path,
        "S0-TIME-04",
        requirement="质量门的局部修复不应原地覆盖原始 aligner 返回值；修复时间不是原始测量。",
        evidence=["src/creator_agent/asr/ja_worker.py:794-833 _gate_alignment"],
        inputs={"raw_alignment": before, "chunk": chunk},
        observed={"raw_alignment_after": raw, "repaired_words": repaired, "check": asdict(check)},
        requirement_met=raw == before,
    )
    assert raw == before
    assert check.ok
    assert repaired[1][1:] == pytest.approx((1.5, 1.55))
    assert "repaired 1" in check.detail


def test_stage0_word_internal_split_pushes_suffix_after_word_end(tmp_path):
    text = "学校へ行ける"
    words = [(text, 1.0, 2.0)]
    cues = split_cues(text, 0.9, 2.1, words=words, max_chars=3)
    _record(
        tmp_path,
        "S0-SPLIT-01",
        requirement="长词内部按字符拆 cue 时，不能把后半文本推到该词 ForcedAligner 结束之后。",
        evidence=[
            "src/creator_agent/asr/cue_splitter.py:237-247 _wrap",
            "src/creator_agent/asr/cue_splitter.py:326-342 _word_timeline",
            "src/creator_agent/asr/cue_splitter.py:132-137 split_cues",
        ],
        inputs={"text": text, "words": words, "max_chars": 3, "chunk": [0.9, 2.1]},
        observed={"cues": cues},
        requirement_met=all(c["end"] <= words[-1][2] for c in cues),
    )
    assert [c["text"] for c in cues] == ["学校へ", "行ける"]
    assert (cues[0]["start"], cues[0]["end"]) == (1.0, 2.0)
    assert (cues[1]["start"], cues[1]["end"]) == (2.0, 2.05)


def test_stage0_proportional_pause_is_dropped_without_punctuation(tmp_path):
    text = "ああああいいいい"
    speech = [(0.0, 2.0), (5.0, 7.0)]
    cues = split_cues(text, 0.0, 7.0, max_chars=24, pause_sec=0.6, speech=speech)
    _record(
        tmp_path,
        "S0-SPLIT-02",
        requirement="speech-aware 比例分配分支宣称长停顿是硬边界，但无标点时实际跨越该停顿。",
        evidence=[
            "src/creator_agent/asr/cue_splitter.py:387-389 _snap_offsets_to_breaks",
            "src/creator_agent/asr/cue_splitter.py:470-471 _speech_timeline",
            "tests/unit/test_cue_splitter.py:256-262 test_speech_fallback_gap_is_hard_boundary",
        ],
        inputs={"text": text, "speech": speech, "pause_sec": 0.6},
        observed={"cues": cues, "silent_gap_sec": 3.0},
        requirement_met=len(cues) == 2,
    )
    assert cues == [{"start": 0.0, "end": 7.0, "text": text}]


def test_stage0_semantic_short_lines_are_not_length_filtered(tmp_path):
    texts = ["うん", "ううん", "はい", "いや", "そう", "違う", "何？", "えっ、嘘？"]
    cues = [split_cues(text, 1.0, 1.4) for text in texts]
    srt = segments_to_srt([TranscriptSegment(**c) for group in cues for c in group])
    _record(
        tmp_path,
        "S0-TEXT-01",
        requirement="有独立语义的短句在切分和 SRT 渲染层不能仅因字符少而删除；不等于真实音频均可识别。",
        evidence=[
            "src/creator_agent/asr/cue_splitter.py:77-79,185-195 split_cues/_strip_cue_head",
            "src/creator_agent/asr/subtitle.py:55-67 segments_to_srt",
        ],
        inputs={"semantic_short_texts": texts, "duration_each": 0.4},
        observed={"cues": cues, "srt": srt},
        requirement_met=all(any(c["text"] == text for c in group) for text, group in zip(texts, cues)),
    )
    assert all([c["text"] for c in group] == [text] for text, group in zip(texts, cues))
    assert all(text in srt for text in texts)


def test_stage1_repeat_tail_retains_original_asr_text(tmp_path):
    raw_text = "うんうんうん"
    chunk = {"idx": 0, "start": 1.0, "end": 4.0, "reason": "normal", "speaker": None}
    args = SimpleNamespace(language="Japanese", max_new_tokens=1024, repeat_tail_max_cps=0.0)
    chunk_before = copy.deepcopy(chunk)
    stats = Counter()
    with (
        mock.patch.object(ja_worker, "_slice_wav", return_value=tmp_path / "synthetic.wav"),
        mock.patch.object(ja_worker, "_transcribe_chunk", return_value=raw_text),
    ):
        chunks = ja_worker._asr_pass(None, None, [chunk], args, stats, tmp_path, tmp_path / "vocals.wav", [(1, 4)], 1)
    _record(
        tmp_path,
        "S0-TEXT-02",
        requirement="重复尾判定即使裁剪后处理文本，也必须保留完整原始 ASR 文本；不能据字符串证明真幻觉。",
        evidence=["src/creator_agent/asr/ja_worker.py:488-509 _asr_pass"],
        inputs={"asr_returned_text": raw_text, "chunk": chunk_before, "repeat_tail_max_cps": 0.0},
        observed={"asr_chunks": [{**c, "wav": str(c["wav"])} for c in chunks], "stats": dict(stats)},
        requirement_met=chunks[0].get("raw_text") == raw_text,
    )
    assert chunks[0]["text"] == "うん"
    assert chunks[0]["raw_text"] == raw_text
    assert stats["asr_repeat_cut"] == 1


def test_stage1_display_minimum_respects_media_limit(tmp_path):
    cues = [{"start": 9.98, "end": 10.0, "text": "はい"}]
    displayed = finalize_for_srt(cues, min_display_sec=0.7, limit=10.0)
    _record(
        tmp_path,
        "S0-TIME-05",
        requirement="非默认显示时长优化开启后，也不能在裁剪到媒体结束后重新扩展越界。",
        evidence=["src/creator_agent/asr/subtitle.py:96-103 finalize_for_srt"],
        inputs={"cues": cues, "min_display_sec": 0.7, "limit": 10.0},
        observed={"displayed_cues": displayed, "source_cues_after": cues},
        requirement_met=displayed[0]["end"] <= 10.0,
    )
    assert displayed[0]["end"] == 10.0
    assert displayed[0]["diag"]["display_timing"]["flags"] == ["MIN_DISPLAY_UNSATISFIED"]
    assert cues[0]["end"] == 10.0


def test_stage0_failed_force_extraction_can_reuse_damaged_cache(tmp_path):
    video = tmp_path / "synthetic.mp4"
    wav = tmp_path / "synthetic.wav"
    video.write_bytes(b"synthetic source, not media")
    wav.write_bytes(b"synthetic previously successful WAV, not media")
    stat = video.stat()
    sidecar = wav.with_suffix(".wav.src.json")
    sidecar.write_text(json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}), encoding="utf-8")
    metadata_before = sidecar.read_text(encoding="utf-8")

    def failed_extract(*args, **kwargs):
        wav.write_bytes(b"truncated")
        return mock.Mock(returncode=1, stderr="synthetic extraction failure")

    with mock.patch("creator_agent.asr.audio_extractor.subprocess.run", side_effect=failed_extract):
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            extract_audio(video, wav, ffmpeg_path="synthetic-ffmpeg", force=True)
    with mock.patch("creator_agent.asr.audio_extractor.subprocess.run") as rerun:
        cached = extract_audio(video, wav, ffmpeg_path="synthetic-ffmpeg")
    observed = {
        "rerun_ffmpeg_calls": rerun.call_count,
        "cached_bytes": cached.read_bytes().decode(),
        "sidecar_after": sidecar.read_text(encoding="utf-8"),
    }
    _record(
        tmp_path,
        "S0-CACHE-01",
        requirement="同一源强制重提取失败后，旧 sidecar 不应让残损音频在下次非 force 运行命中缓存。",
        evidence=["src/creator_agent/asr/audio_extractor.py:28-40,55-57,61-91 _cache_hit/extract_audio"],
        inputs={"valid_cache_before": True, "source_unchanged": True, "force_extract_returncode": 1},
        observed=observed,
        requirement_met=rerun.call_count > 0,
    )
    rerun.assert_not_called()
    assert cached.read_bytes() == b"truncated"
    assert sidecar.read_text(encoding="utf-8") == metadata_before
