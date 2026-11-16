# 日语 ASR 阶段 0 — 自动审计复现报告

运行编号：`20261116T072406.355863Z-d7ca6063`；代码基准：`7dc264df22acca8699daef81c1db4cc8060963c3`。

## 目标与范围
仅复现当前代码问题、建立离线基线；不修复生产算法，不进入阶段 1，不执行翻译。
这是代码/合成数据证据，不能替代真实日语音频验收。源文件版本见 manifest.json。

## 检查函数、问题证据与未解决事项

### S0-TIME-02 — NOT_MET
边界检查不应为满足 min_dur 在媒体末尾制造字幕重叠。

- `src/creator_agent/asr/boundary_check.py:262-270 refine_cues`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "cues": [
      {
        "start": 9.5,
        "end": 9.9,
        "text": "はい"
      },
      {
        "start": 9.95,
        "end": 10.0,
        "text": "うん"
      }
    ],
    "silences": [],
    "duration": 10.0
  },
  "observed": {
    "cues": [
      {
        "start": 9.5,
        "end": 9.9,
        "text": "はい"
      },
      {
        "start": 9.8,
        "end": 10.0,
        "text": "うん"
      }
    ],
    "flags": [],
    "overlap_sec": 0.09999999999999964
  }
}
```

### S0-TIME-01 — NOT_MET
silencedetect 默认仅诊断，不覆盖 ForcedAligner 时间；原始对齐时间应可追溯。

- `src/creator_agent/asr/boundary_check.py:205-228 snap_edge`
- `src/creator_agent/asr/ja_transcriber.py:207-234 _check_boundaries`
- `src/creator_agent/asr/review.py:84-101 build_review`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "worker_result": {
      "video_id": "synthetic",
      "ok": true,
      "text": "はい",
      "duration_sec": 5.0,
      "segments": [
        {
          "start": 1.35,
          "end": 2.0,
          "text": "はい",
          "diag": {
            "time_source": "word_timed"
          }
        }
      ]
    },
    "silences": [
      [
        0.0,
        1.24
      ]
    ]
  },
  "observed": {
    "after_boundary": {
      "video_id": "synthetic",
      "ok": true,
      "text": "はい",
      "duration_sec": 5.0,
      "segments": [
        {
          "start": 1.16,
          "end": 2.0,
          "text": "はい",
          "diag": {
            "time_source": "word_timed",
            "boundary": "snapped_start"
          }
        }
      ],
      "boundary_report": "边界检查报告: synthetic\nvideo duration : 00:05.000\naudio duration : 00:05.000\ncues           : 1（吸附调整 1 条，可疑 0 条）\n\n所有 cue 边界均落在静音附近，无可疑项。\n"
    },
    "transcript": {
      "video_id": "synthetic",
      "text": "はい",
      "segments": [
        {
          "start": 1.16,
          "end": 2.0,
          "text": "はい",
          "speaker": null
        }
      ],
      "language": "ja",
      "model": "Qwen/Qwen3-ASR-1.7B-hf",
      "duration_sec": 5.0,
      "created_at": "2026-11-16T07:24:07.172875Z"
    },
    "review": {
      "meta": {
        "source": "synthetic.mp4",
        "duration_sec": 5.0,
        "model": "Qwen/Qwen3-ASR-1.7B-hf",
        "overrides": {
          "vad_threshold": 0.5,
          "vad_min_speech_ms": 120,
          "vad_min_silence_ms": 300,
          "vad_speech_pad_ms": 150,
          "max_cue_chars": 24,
          "max_cue_sec": 8.0,
          "pause_sec": 0.6,
          "recover_missed": false,
          "recover_min_sec": 0.4,
          "recover_vad_threshold": 0.5,
          "repeat_tail_max_cps": 0.0,
          "chunk_target_min_sec": 3.0,
          "chunk_target_max_sec": 8.0,
          "max_chunk_sec": 15.0,
          "asr_max_new_tokens": 1024,
          "align_pad_sec": 0.3,
          "align_min_coverage": 0.5,
          "srt_min_display_sec": 0.0,
          "srt_lead_in_sec": 0.0,
          "srt_tail_sec": 0.0,
          "boundary_check": true,
          "boundary_snap_sec": 0.5,
          "boundary_silence_db": -35.0,
          "boundary_min_silence_sec": 0.12,
          "boundary_lead_in_sec": 0.08,
          "boundary_tail_sec": 0.1,
          "speaker_threshold": 0.5
        },
        "summary": {}
      },
      "cues": [
        {
          "id": 1,
          "start": 1.16,
          "end": 2.0,
          "speech_start": 1.16,
          "speech_end": 2.0,
          "text": "はい",
          "speaker": null,
          "diag": {
            "time_source": "word_timed",
            "boundary": "snapped_start"
          },
          "question": "",
          "expected": ""
        }
      ],
      "dropped": [],
      "uncovered": []
    }
  }
}
```

### S0-QC-01 — NOT_MET
同一输出目录重跑时，应标明 sidecar 所属运行，避免旧记录冒充当前结果；保留旧数据。

- `src/creator_agent/asr/ja_transcriber.py:410-420 _write_outputs`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "first": {
      "text": "はい",
      "duration_sec": 5.0,
      "segments": [
        {
          "start": 1.0,
          "end": 2.0,
          "text": "はい"
        }
      ],
      "dropped": [
        {
          "start": 3.0,
          "end": 4.0,
          "text": "うん",
          "speech_cov": 0.1
        }
      ],
      "boundary_report": "synthetic first-run boundary report\n"
    },
    "second": {
      "text": "はい",
      "duration_sec": 5.0,
      "segments": [
        {
          "start": 1.0,
          "end": 2.0,
          "text": "はい"
        }
      ],
      "dropped": [],
      "boundary_report": null
    }
  },
  "observed": {
    "dropped_txt": "[3.00-4.00] speech_cov=0.10  うん\n",
    "check_txt": "synthetic first-run boundary report\n",
    "reviews": [
      {
        "meta": {
          "source": "synthetic.mp4",
          "duration_sec": 5.0,
          "model": "Qwen/Qwen3-ASR-1.7B-hf",
          "overrides": {
            "vad_threshold": 0.5,
            "vad_min_speech_ms": 120,
            "vad_min_silence_ms": 300,
            "vad_speech_pad_ms": 150,
            "max_cue_chars": 24,
            "max_cue_sec": 8.0,
            "pause_sec": 0.6,
            "recover_missed": false,
            "recover_min_sec": 0.4,
            "recover_vad_threshold": 0.5,
            "repeat_tail_max_cps": 0.0,
            "chunk_target_min_sec": 3.0,
            "chunk_target_max_sec": 8.0,
            "max_chunk_sec": 15.0,
            "asr_max_new_tokens": 1024,
            "align_pad_sec": 0.3,
            "align_min_coverage": 0.5,
            "srt_min_display_sec": 0.0,
            "srt_lead_in_sec": 0.0,
            "srt_tail_sec": 0.0,
            "boundary_check": true,
            "boundary_snap_sec": 0.5,
            "boundary_silence_db": -35.0,
            "boundary_min_silence_sec": 0.12,
            "boundary_lead_in_sec": 0.08,
            "boundary_tail_sec": 0.1,
            "speaker_threshold": 0.5
          },
          "summary": {}
        },
        "cues": [
          {
            "id": 1,
            "start": 1.0,
            "end": 2.0,
            "speech_start": 1.0,
            "speech_end": 2.0,
            "text": "はい",
            "speaker": null,
            "diag": {
              "boundary": null
            },
            "question": "",
            "expected": ""
          }
        ],
        "dropped": [],
        "uncovered": []
      },
      {
        "meta": {
          "source": "synthetic.mp4",
          "duration_sec": 5.0,
          "model": "Qwen/Qwen3-ASR-1.7B-hf",
          "overrides": {
            "vad_threshold": 0.5,
            "vad_min_speech_ms": 120,
            "vad_min_silence_ms": 300,
            "vad_speech_pad_ms": 150,
            "max_cue_chars": 24,
            "max_cue_sec": 8.0,
            "pause_sec": 0.6,
            "recover_missed": false,
            "recover_min_sec": 0.4,
            "recover_vad_threshold": 0.5,
            "repeat_tail_max_cps": 0.0,
            "chunk_target_min_sec": 3.0,
            "chunk_target_max_sec": 8.0,
            "max_chunk_sec": 15.0,
            "asr_max_new_tokens": 1024,
            "align_pad_sec": 0.3,
            "align_min_coverage": 0.5,
            "srt_min_display_sec": 0.0,
            "srt_lead_in_sec": 0.0,
            "srt_tail_sec": 0.0,
            "boundary_check": true,
            "boundary_snap_sec": 0.5,
            "boundary_silence_db": -35.0,
            "boundary_min_silence_sec": 0.12,
            "boundary_lead_in_sec": 0.08,
            "boundary_tail_sec": 0.1,
            "speaker_threshold": 0.5
          },
          "summary": {}
        },
        "cues": [
          {
            "id": 1,
            "start": 1.0,
            "end": 2.0,
            "speech_start": 1.0,
            "speech_end": 2.0,
            "text": "はい",
            "speaker": null,
            "diag": {
              "boundary": null
            },
            "question": "",
            "expected": ""
          }
        ],
        "dropped": [
          {
            "start": 3.0,
            "end": 4.0,
            "text": "うん",
            "speech_cov": 0.1,
            "question": ""
          }
        ],
        "uncovered": []
      }
    ]
  }
}
```

## 自动测试结果

characterization PASS 只表示问题复现成功；各 case 的 requirement_status 才表示需求是否满足。

- baseline: **FAIL**，exit=1，{'tests': 130, 'failures': 1, 'errors': 0, 'skipped': 0}；详见 baseline.log。
- characterization: **PASS**，exit=0，{'tests': 3, 'failures': 0, 'errors': 0, 'skipped': 0}；详见 characterization.log。
- ruff_check: **PASS**，exit=0，{}；详见 ruff_check.log。
- ruff_format: **PASS**，exit=0，{}；详见 ruff_format.log。

## 未运行的验证

- Windows 04_ja_asr.bat + dual conda envs: **NOT RUN**。
- BS-RoFormer / Silero / CAM++ / Qwen3-ASR / ForcedAligner inference: **NOT RUN**。
- 真实音频听校、日文识别率、短句漏识别率、对齐误差: **NOT RUN**。
- 全仓库测试（本次限定日语 ASR 相关单元测试）: **NOT RUN**。

## 修改内容及前后区别

- 新增 `scripts/audit_ja_asr_stage0.py`：仅运行离线审计测试。
  保留独立运行编号、源文件哈希、原始输入输出、日志和 JUnit/JSON。
- 新增 `tests/unit/test_ja_asr_stage0_audit.py`：固定当前问题的纯函数/模拟复现。PASS 不等于字幕质量合格。
- 未修改 `src/`、bat、模型、ForcedAligner、配置参数、缓存、断点续跑或恢复逻辑。
- 修改前后：生产字幕行为相同；新增可复现证据。后续修复对应阶段时，应把问题刻画测试转为修复回归测试。
- 本运行目录仅含合成输入和测试产物，无用户视频、真实音频或模型输出。

## 用户本地验证与停止点
1. 在项目根目录运行 `uv run python scripts/audit_ja_asr_stage0.py`，每次自动新建运行目录，不覆盖旧结果。
2. 核对 baseline.log 中已有失败与 characterization.log 的问题复现；非零退出不能当作 PASS。
3. 如需跑真实视频，使用现有 04_ja_asr.bat，指定全新 --out-dir；不要传 --translate / --burn。
4. 保留原始视频、人声音频、SRT、TXT、transcript、review、check、dropped 与终端日志，人工核对目标短句及边界。
5. 此阶段未修复字幕质量；完成本地核对后由用户确认，才开始阶段 1。
