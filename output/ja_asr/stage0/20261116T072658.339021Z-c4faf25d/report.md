# 日语 ASR 阶段 0 — 自动审计复现报告

运行编号：`20261116T072658.339021Z-c4faf25d`；代码基准：`7dc264df22acca8699daef81c1db4cc8060963c3`。

## 目标与范围
仅复现当前代码问题、建立离线基线；不修复生产算法，不进入阶段 1，不执行翻译。
这是代码/合成数据证据，不能替代真实日语音频验收。源文件版本见 manifest.json。

## 检查函数、问题证据与未解决事项

### S0-TIME-03 — MET
局部词时间应加实际对齐窗口起点一次，不漏加、重复加或使用未带 pad 的 ASR 起点。

- `src/creator_agent/asr/aligner_worker.py:80-90 _align_one`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "job": {
      "wav": "synthetic.wav",
      "text": "はい",
      "chunk_start": 9.7
    },
    "local_words": [
      [
        "はい",
        0.3,
        0.8
      ]
    ],
    "asr_chunk_start": 10.0
  },
  "observed": {
    "global_words": [
      [
        "はい",
        10.0,
        10.5
      ]
    ],
    "local_words_after": [
      [
        "はい",
        0.3,
        0.8
      ]
    ]
  }
}
```

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
      "created_at": "2026-11-16T07:26:59.161264Z"
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

### S0-TIME-05 — NOT_MET
非默认显示时长优化开启后，也不能在裁剪到媒体结束后重新扩展越界。

- `src/creator_agent/asr/subtitle.py:96-103 finalize_for_srt`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "cues": [
      {
        "start": 9.98,
        "end": 10.0,
        "text": "はい"
      }
    ],
    "min_display_sec": 0.7,
    "limit": 10.0
  },
  "observed": {
    "displayed_cues": [
      {
        "start": 9.98,
        "end": 10.03,
        "text": "はい"
      }
    ],
    "source_cues_after": [
      {
        "start": 9.98,
        "end": 10.0,
        "text": "はい"
      }
    ]
  }
}
```

### S0-CACHE-01 — NOT_MET
同一源强制重提取失败后，旧 sidecar 不应让残损音频在下次非 force 运行命中缓存。

- `src/creator_agent/asr/audio_extractor.py:28-40,55-57,61-91 _cache_hit/extract_audio`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "valid_cache_before": true,
    "source_unchanged": true,
    "force_extract_returncode": 1
  },
  "observed": {
    "rerun_ffmpeg_calls": 0,
    "cached_bytes": "truncated",
    "sidecar_after": "{\"size\": 27, \"mtime_ns\": 1794814019174461087}"
  }
}
```

### S0-TIME-04 — MET
质量门的局部修复不应原地覆盖原始 aligner 返回值；修复时间不是原始测量。

- `src/creator_agent/asr/ja_worker.py:794-833 _gate_alignment`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "raw_alignment": {
      "ok": true,
      "words": [
        [
          "はい",
          1.0,
          1.5
        ],
        [
          "そう",
          1.5,
          1.5
        ],
        [
          "です",
          1.6,
          2.0
        ]
      ]
    },
    "chunk": {
      "start": 0.9,
      "end": 2.1,
      "text": "はいそうです"
    }
  },
  "observed": {
    "raw_alignment_after": {
      "ok": true,
      "words": [
        [
          "はい",
          1.0,
          1.5
        ],
        [
          "そう",
          1.5,
          1.5
        ],
        [
          "です",
          1.6,
          2.0
        ]
      ]
    },
    "repaired_words": [
      [
        "はい",
        1.0,
        1.5
      ],
      [
        "そう",
        1.5,
        1.55
      ],
      [
        "です",
        1.6,
        2.0
      ]
    ],
    "check": {
      "ok": true,
      "reason": "",
      "coverage": 1.0,
      "detail": "repaired 1 zero/short word time(s)"
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

### S0-SPLIT-02 — NOT_MET
speech-aware 比例分配分支宣称长停顿是硬边界，但无标点时实际跨越该停顿。

- `src/creator_agent/asr/cue_splitter.py:387-389 _snap_offsets_to_breaks`
- `src/creator_agent/asr/cue_splitter.py:470-471 _speech_timeline`
- `tests/unit/test_cue_splitter.py:256-262 test_speech_fallback_gap_is_hard_boundary`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "text": "ああああいいいい",
    "speech": [
      [
        0.0,
        2.0
      ],
      [
        5.0,
        7.0
      ]
    ],
    "pause_sec": 0.6
  },
  "observed": {
    "cues": [
      {
        "start": 0.0,
        "end": 7.0,
        "text": "ああああいいいい"
      }
    ],
    "silent_gap_sec": 3.0
  }
}
```

### S0-TEXT-02 — NOT_MET
重复尾判定即使裁剪后处理文本，也必须保留完整原始 ASR 文本；不能据字符串证明真幻觉。

- `src/creator_agent/asr/ja_worker.py:488-509 _asr_pass`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "asr_returned_text": "うんうんうん",
    "chunk": {
      "idx": 0,
      "start": 1.0,
      "end": 4.0,
      "reason": "normal",
      "speaker": null
    },
    "repeat_tail_max_cps": 0.0
  },
  "observed": {
    "asr_chunks": [
      {
        "idx": 0,
        "start": 1.0,
        "end": 4.0,
        "reason": "normal",
        "speaker": null,
        "repeat_tail_cut": true,
        "wav": "/Users/intasect/Music/g/creator-agent/output/ja_asr/stage0/20261116T072658.339021Z-c4faf25d/raw/characterization/test_stage0_repeat_tail_change0/synthetic.wav",
        "text": "うん"
      }
    ],
    "stats": {
      "asr_repeat_cut": 1,
      "asr_ok": 1
    }
  }
}
```

### S0-TEXT-01 — MET
有独立语义的短句在切分和 SRT 渲染层不能仅因字符少而删除；不等于真实音频均可识别。

- `src/creator_agent/asr/cue_splitter.py:77-79,185-195 split_cues/_strip_cue_head`
- `src/creator_agent/asr/subtitle.py:55-67 segments_to_srt`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "semantic_short_texts": [
      "うん",
      "ううん",
      "はい",
      "いや",
      "そう",
      "違う",
      "何？",
      "えっ、嘘？"
    ],
    "duration_each": 0.4
  },
  "observed": {
    "cues": [
      [
        {
          "start": 1.0,
          "end": 1.4,
          "text": "うん"
        }
      ],
      [
        {
          "start": 1.0,
          "end": 1.4,
          "text": "ううん"
        }
      ],
      [
        {
          "start": 1.0,
          "end": 1.4,
          "text": "はい"
        }
      ],
      [
        {
          "start": 1.0,
          "end": 1.4,
          "text": "いや"
        }
      ],
      [
        {
          "start": 1.0,
          "end": 1.4,
          "text": "そう"
        }
      ],
      [
        {
          "start": 1.0,
          "end": 1.4,
          "text": "違う"
        }
      ],
      [
        {
          "start": 1.0,
          "end": 1.4,
          "text": "何？"
        }
      ],
      [
        {
          "start": 1.0,
          "end": 1.4,
          "text": "えっ、嘘？"
        }
      ]
    ],
    "srt": "1\n00:00:01,000 --> 00:00:01,400\nうん\n\n2\n00:00:01,000 --> 00:00:01,400\nううん\n\n3\n00:00:01,000 --> 00:00:01,400\nはい\n\n4\n00:00:01,000 --> 00:00:01,400\nいや\n\n5\n00:00:01,000 --> 00:00:01,400\nそう\n\n6\n00:00:01,000 --> 00:00:01,400\n違う\n\n7\n00:00:01,000 --> 00:00:01,400\n何？\n\n8\n00:00:01,000 --> 00:00:01,400\nえっ、嘘？\n"
  }
}
```

### S0-SPLIT-01 — NOT_MET
长词内部按字符拆 cue 时，不能把后半文本推到该词 ForcedAligner 结束之后。

- `src/creator_agent/asr/cue_splitter.py:237-247 _wrap`
- `src/creator_agent/asr/cue_splitter.py:326-342 _word_timeline`
- `src/creator_agent/asr/cue_splitter.py:132-137 split_cues`

输入与实际输出（完整原始数据另存 results.json 和 raw/）：
```json
{
  "inputs": {
    "text": "学校へ行ける",
    "words": [
      [
        "学校へ行ける",
        1.0,
        2.0
      ]
    ],
    "max_chars": 3,
    "chunk": [
      0.9,
      2.1
    ]
  },
  "observed": {
    "cues": [
      {
        "start": 1.0,
        "end": 2.0,
        "text": "学校へ"
      },
      {
        "start": 2.0,
        "end": 2.05,
        "text": "行ける"
      }
    ]
  }
}
```

## 自动测试结果

characterization PASS 只表示问题复现成功；各 case 的 requirement_status 才表示需求是否满足。

- baseline: **FAIL**，exit=1，{'tests': 130, 'failures': 1, 'errors': 0, 'skipped': 0}；详见 baseline.log。
- characterization: **PASS**，exit=0，{'tests': 11, 'failures': 0, 'errors': 0, 'skipped': 0}；详见 characterization.log。
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
