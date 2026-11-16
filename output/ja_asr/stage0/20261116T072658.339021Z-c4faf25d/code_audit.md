# 日语 ASR 质量专项 — 阶段 0 代码审计报告

## 1. 本阶段目标与停止点

建立当前日语 ASR 的代码基线、时间/文本来源链与可重复的问题证据。本阶段仅新增审计脚本和自动测试，**不修复生产算法、不调模型参数、不更换 ASR/ForcedAligner、不翻译、不开发审核页面**。未自动进入阶段 1。

- 最终运行编号：`20261116T072658.339021Z-c4faf25d`。
- 审计基准：`7dc264df22acca8699daef81c1db4cc8060963c3`。
- 实际测试环境：macOS ARM64、uv 主环境 Python **3.14.7**、没有 torch。项目要求为 Python >=3.12；**Python 3.12 / Windows 双 conda 环境尚未验证（NOT RUN）**。
- 没有运行模型、真实视频/音频、分离器或 ffmpeg 推理流程；原始模拟输入、实际结果与 JUnit 见本目录 `results.json`、`raw/characterization/**/case.json`、`*.log`、`*.xml`。
- `manifest.json` 记录源文件 SHA-256；包含哈希不代表其中所有包文件都属于本次审阅范围，实际审阅清单见下节。
- 旧文档 `docs/ja-asr-audit-2026-10-17.md` 仅作为线索，未把其旧音频结论、错误数或调参建议当成本次证据。

**结论：阶段 0 取证完成；字幕质量验收 NOT RUN。下阶段待用户本地核对并明确确认。**

## 2. 阅读文件与函数

| 文件 | 检查函数/路径 |
|---|---|
| `bat/doraemon/04_ja_asr.bat` | bootstrap、主环境入口、每次桌面新目录、显式参数传递；本任务不传翻译参数 |
| `src/creator_agent/cli/main.py:436-550` | `_apply_validate_overrides`、`ja_asr` 的配置覆盖、扫描、force、按 SRT 跳过、调用转录器；翻译条件仅确认不会默认进入 |
| `src/creator_agent/config.py:39-134` | `JaAsrSettings` 默认值 |
| `config/settings.yaml:29-87`、`config/settings.validate.yaml` | 日语 ASR 有效配置和自动覆盖；未修改任何值 |
| `asr/audio_extractor.py` | `find_ffmpeg`、`_sidecar_path`、`_cache_hit`、`extract_audio` |
| `asr/ja_transcriber.py` | `scan_videos`、`split_pending`、`transcribe_files`、`_check_boundaries`、`_run_worker`、`_write_outputs`、结果协议 |
| `asr/ja_worker.py` | 模型/音轨加载、`_separate_vocals`、`_to_16k_mono`、`_speech_intervals`、`_diarize`、`_transcribe_chunk`、`_asr_pass`、`_align_stream`、`_process_one`、`_gate_alignment`、`_print_summary`、`_slice_wav`、`main` |
| `asr/aligner_worker.py` | `_load_aligner`、`_align_one`、逐块 marker 与最终 sentinel、逐块异常 |
| `asr/align_check.py` | `repair_word_timing`、`check_alignment` |
| `asr/cue_splitter.py` | `split_cues`、`_wrap`、`_word_timeline`、字符匹配、比例时间线、pause snap、重复尾、跨块 overlap |
| `asr/vad_chunker.py` | `build_chunks`、旧分块路径、`subtract_intervals`、`merge_close`、`speech_coverage`、`uncovered_ranges` |
| `asr/speaker_turns.py` | 标签平滑、说话人分块、speaker names；实际 CAM++ 推理 NOT RUN |
| `asr/boundary_check.py` | ffprobe/silencedetect 解析、`refine_cues`、`format_check_report` |
| `asr/subtitle.py`、`asr/review.py`、`models/transcript.py` | SRT 显示优化、渲染、review 双时间字段、历史写入、Pydantic 导出 |
| 现有相关测试 | audio_extractor、ja_transcriber、boundary_check、review、align_check、cue_splitter、subtitle、build_chunks、vad_chunker 共九组；执行结果下列 |

上表 `asr/` 为 `src/creator_agent/asr/` 的简称。未审计中文翻译代码。

## 3. 有依据的时间坐标链

1. `extract_audio` 抽取整个源视频的 16kHz 单声道 PCM WAV，无 `-ss` 局部截取。
2. 人声分离后 `_to_16k_mono` 重采样完整 vocals。主 VAD、tight VAD、较低阈值 recovery VAD 都针对同一个完整 vocals。
3. `_speech_intervals:279-290` 将 Silero 的样本坐标除以 16000，形成完整 vocals 坐标中的秒数。
4. ASR chunk 是完整 vocals 中的绝对窗口；`_slice_wav:899-905` 使用 `int(start * sr):int(end * sr)`。
5. `_align_stream:395-400` 实际对齐窗口为 `astart=max(0, chunk.start-align_pad)` 到 `min(duration, chunk.end+align_pad)`。传给 aligner 的 `chunk_start` 是 **astart**。
6. `_align_one:86-90` 只加该起点一次。已用局部 `0.3-0.8`、起点 `9.7` 验证得到绝对 `10.0-10.5`，没有原地改局部值。
7. 后续 gate、切分、overlap、boundary 和 SRT 显示优化可能改变时刻；目前没有保存每一层前后的完整证据。

**未发现对齐起点漏加/重复加的直接代码错误。** 未验证实际分离器延迟、音视频流 start_time、重采样长度与声学发音位置；不把这些猜测写成真实偏移。VAD/silencedetect 的检测边界不等于已经测得的发音边界。

## 4. 已运行的证据

11 项测试中 3 项证明当前某个局部契约成立、8 项复现问题。**测试 PASS = 实际行为符合该问题刻画，不等于需求 MET，更不等于模型识别准确。** 每项原始输入/输出及需求状态在 `results.json`。

### S0-TIME-01：默认 silencedetect 吸附覆盖 word_timed 时间

- 位置：`boundary_check.py:205-228`；`ja_transcriber.py:207-234,382-435`；`review.py:84-101`。
- 合成 worker cue：`1.35-2.0`，`diag.time_source=word_timed`；silence：`0-1.24`。
- **实际输出**：cue start 改为 **1.16**；`transcript.json.start`、`review.speech_start` 也都是 1.16。review 仍标 `word_timed`，没有可疑 flags；只有 `snapped_start` 动作，没有原始起点字段。
- Pydantic `TranscriptSegment` 只导出 start/end/text/speaker，连 diag 也不进 transcript。
- 原始对齐时刻无法由最终产物完整还原。此处把检测吸附后的值称为 speech time，会混淆测量、修复和展示。
- **阶段 1 最小方向，未实施**：将 silencedetect 默认用于诊断；保存 pre-boundary/raw word/cue 时间、变更原因与最终显示时间。不要因静音距离近就覆盖 ForcedAligner。

### S0-TIME-02：视频末尾的 min_dur 制造重叠

- 位置：`boundary_check.py:262-270`。
- 输入：`9.5-9.9 はい`、`9.95-10.0 うん`，媒体长 10，无 silence。
- **实际输出**：第二条变为 `9.8-10.0`，与第一条重叠 **0.1 秒**，flags 为空。
- 原因：先约束 prev_end，最后又把 start 回退到 end-min_dur。
- **阶段 1 最小方向，未实施**：媒体范围/顺序约束优先；无法满足最短时长时保留并标记，不反向移动真实时间来满足时长。

### S0-TIME-03 / 04：已确认的正确局部行为，不扩大解释

- `_align_one` 窗口起点加一次：**MET**，见上节。
- `_gate_alignment:794-833` 在合成 `そう 1.5-1.5` 上生成 `1.5-1.55` 的修复值，并报告 repaired 1；原始 rec 不变：**MET**。
- 注意：这只证明内存不原地覆盖，**不证明完整 raw rec 被持久化**。修复值仍与原始测量不同，最终 `word_timed` 标签不包含完整逐词修复信息。

### S0-TIME-05：可选显示优化会突破媒体限制

- 位置：`subtitle.py:96-103`。
- 非默认测试：cue `9.98-10.0`，`min_display_sec=0.7`、limit=10。
- **实际输出**：end **10.03**；输入 cue 的 end 仍为 10。
- 原因：先裁剪 limit，再用 `max(e, s+0.05)` 扩展。**只有该显示优化分支启用时触发；当前全零显示优化配置不走此路径。**
- **阶段 1 最小方向，未实施**：最终硬约束不能被最短显示时长再覆盖；保留原 cue，仅标记无法满足显示预算。

### S0-SPLIT-01：词内字符切分把后半文本推到词结束之后

- 位置：`cue_splitter.py:237-247,326-342,132-137`。
- 合成单词：`学校へ行ける 1.0-2.0`，max_chars=3，chunk=`0.9-2.1`。
- **实际输出**：`学校へ 1.0-2.0`、`行ける 2.0-2.05`。
- 两部分原本都映射到同一个词的完整 start/end，随后非重叠修复把后半推走。max_chars=3 是边界复现参数，不是模型调参，也不证明默认行长下此例必现；默认预算和大 token 组合的真实触发率 NOT RUN。
- **阶段 3 最小方向，未实施**：优先词边界；若确需词内估算，显式标记估算来源且保持在原词范围，不把顺序修复当真实对齐。

### S0-SPLIT-02：比例时间线无标点时跨越长停顿

- 位置：`cue_splitter.py:387-389,470-471`；既有测试 `test_cue_splitter.py:256-262`。
- 输入：`ああああいいいい`，speech=`[(0,2),(5,7)]`，pause_sec=0.6。
- **实际输出**：`0-7` 单条 cue，跨过 3 秒静音；既有测试要求两条，因此 **FAIL**。
- `_snap_offsets_to_breaks` 找不到标点就丢弃 pause。文件头记录了这个新策略，但函数仍承诺长停顿是硬边界，形成契约冲突。
- **阶段 3 最小方向，未实施**：不要为躲避词内切分而静默丢停顿证据；结合文本/对齐选择边界，无法可信切分时保留并标记。不能仅把旧测试改成“期待1条”来消除失败。

### S0-TEXT-01 / 02：短句局部保留成立，但重复尾提前改写原文

- split_cues / segments_to_srt 已验证保留 `うん、ううん、はい、いや、そう、違う、何？、えっ、嘘？`（这些是分别测试的句子，不是逗号连接的单句）：**MET**。
- 该测试不执行 VAD/ASR/coverage filter，不代表这些台词在真实音频中无漏识别。
- `_asr_pass:488-509` 使用现有默认 `repeat_tail_max_cps=0.0`，mock ASR 返回 `うんうんうん`。
- **实际输出**：进入后续对齐的 text=`うん`，计数 `asr_repeat_cut=1`，没有 `raw_text` 字段。
- 不能仅凭该字符串断言真实内容是幻觉或真实附和；确定的问题是**后处理改写前原文未保留**。
- **阶段 2 最小方向，未实施**：先保存完整原文和重复尾候选证据；不在 raw ASR 记录中删除。无新证据不调整 repeat-tail/VAD 参数。

### S0-CACHE-01：force 提取失败后复用残损音频

- 位置：`audio_extractor.py:28-40,55-57,61-91`。
- 前提：已有有效缓存、同一源文件未变化；mock ffmpeg 原位写出 `truncated` 后返回1，force 调用抛异常。
- **实际输出**：下一次非 force 调用不执行 ffmpeg，返回残损字节；旧 sidecar 未变。
- 只适用于明确前提；源文件 size/mtime 改变时旧 sidecar 仍会阻止命中，不能泛化成所有失败都误缓存。
- **后续最小方向，未实施**：保留缓存能力，用成功后原子替换/完整性验证防止旧成功标记对应新残损 WAV，不删除整个工作目录。

### S0-QC-01：同目录重跑可能读到上一轮 sidecar

- 位置：`ja_transcriber.py:410-420`。
- 首轮有 dropped/check，次轮 dropped=[] 且 boundary_report=None。
- **实际输出**：两个 timestamp review 都保留，最新 review dropped 为空；无版本号的 dropped.txt/check.txt 仍写着首轮数据。
- 桌面 bat 默认每次新目录会减轻此问题，但直接 CLI / 指定相同 out-dir / force 重跑仍可触发。
- **阶段 4 最小方向，未实施**：用运行标识关联所有 QC 文件，保留历史并明确“当前为空/检查未运行”，不能直接删除旧证据。

## 5. 静态确认、尚未运行的跨流程问题

以下是当前控制流/数据契约证据；**对应完整 worker mock、实际模型/音频验证均 NOT RUN**。不得与上节可执行样例混算。

| ID | 当前代码证据 | 影响与最小后续方向（未实施） |
|---|---|---|
| S0-STATIC-01 | `ja_worker.py:623-632` 用 `recover_missed and tight` 作为低阈值 recovery 检测入口 | tight=[] 时独立 recovery VAD 不运行，即便低阈值本可能检出弱语音。阶段2先测试此分支，再解除不合理依赖；不改阈值。 |
| S0-STATIC-02 | `ja_worker.py:633-634` 用成功 ASR chunk 的整个跨度作 covered；aligner 在662才运行 | 非空开头文本也覆盖整个 chunk，内部漏句不能因此进入补漏。阶段2先区分“chunk处理成功”与“文本/词覆盖”，保留检测候选；不能宣称字符匹配 coverage 是语音完整率。 |
| S0-STATIC-03 | `ja_worker.py:640-657` 对 missed 区间直接 ±0.3 后 ASR，不走 build_chunks；可选 speaker 路径使用旧 merge 函数 | recovery 长候选不受同一硬上限约束；可选 speaker 单超长 interval 也可保留。阶段2复用现有分块器的硬上限，保留来源；默认 speaker_model为空。 |
| S0-STATIC-04 | `ja_worker.py:697,717` 用 recovery 给补漏 cue 算过滤覆盖，753的 diag.speech_cov 却总用 tight；767的 uncovered 只看初始 intervals、min_sec=1 | 放行的恢复 cue 可在diag呈现0；补漏独有语音失败后不一定进入 uncovered，短于1秒缺口也不报告。阶段4明确检测基线、原始区间及统计口径；空 uncovered 不等于无漏句。 |
| S0-STATIC-05 | `ja_worker.py:523,781-790` 的返回值无raw ASR/raw词/修复词/逐块窗口/VAD序列；临时目录退出后只另存音轨 | 无法不重跑模型重放整个后处理；普通文本预览和两位小数 verbose 不是原始结果备份。阶段1开始补足最小诊断旁路，采用向后兼容额外字段，保留原运行方式。 |
| S0-STATIC-06 | `ja_worker.py:484,711-736` 无字符数短句门，只有空文本、重复尾、coverage；`subtitle.py:55-67` 明确不做 filler 过滤 | 当前没有用户要求的上下文语义 filler QC；不能仅按「あ/え/ん」字符数增删。阶段2/3实现保守语义候选分类，原文保留、独立语义短句保护，过滤原因单列。实际语义误删率 NOT RUN。 |

其他边界：逐块 ASR 异常会记录计数并继续，正常逐视频失败隔离已有；完整 batch 最后才输出结果 sentinel，主进程随后才写产物。全部空/失败 chunk 仍可能生成 ok 视频结果；SRT 存在就是 CLI 的已完成判定。没有跨模型中断后的逐 chunk checkpoint。此次不删除、不重构既有 resumability，而是在后续设计时避免扩大这些缺口。

对齐 subprocess 正常退出时已收到的逐块记录可保留并降级缺块；读流/wait 没有超时。aligner 模型加载失败只返回最终 sentinel，而 worker 只解析逐块 marker，结构化错误可能表现为 SUBPROCESS，详细错误仍在 stderr。实际挂起/错误恢复模拟 **NOT RUN**。

## 6. 最小变更方案与实际修改

本阶段不把所有审计发现一次性修复，避免跨阶段改变行为后无从定位质量差异。

1. 增加 `tests/unit/test_ja_asr_stage0_audit.py`：11个纯函数/mock问题刻画与局部契约测试。没有 torch/model/真实媒体依赖。
2. 增加 `scripts/audit_ja_asr_stage0.py`：沿用 `uv run`，执行原有九组测试及新增测试，生成 UTC微秒+随机后缀运行编号、SHA-256、JSON、Markdown、JUnit、日志和原始模拟产物。
3. 每次采用独立目录；拒绝复用 pytest basetemp，避免 pytest 覆盖历史目录。首轮脚本漏建 raw 父目录已修正，失败结果保留。
4. 只提交本次审计代码和精选 `output/ja_asr/stage0/` 文本证据；保留项目原有 `/output/` 忽略规则，不把其它运行媒体或 `.claude/worktrees/` 扫入提交。

**修改前后：生产 ASR/字幕行为完全相同；新增离线可执行诊断与取证。** `src/`、bat、配置、缓存、日志与异常恢复实现没有改动。完整代码变更见 `code_changes.patch`；概要见 `changes.md`。

后续阶段仅给出归属和方向，**不是对阶段1-4执行的授权**。每个阶段仍必须单独读代码、证据→方案→修改→测试→OUTPUT→用户本地确认。

## 7. 自动测试结果与保留的失败历史

| 运行编号 | 既有九组测试 | 新增问题刻画 | Ruff | 说明 |
|---|---|---|---|---|
| `20261116T071859.005227Z-1250a10b` | 130项中1 failure、21 errors | 3 errors | check/format PASS | 脚本未创建 raw 父目录，tmp_path准备失败；不是模型结果。完整日志保留。 |
| `20261116T072406.355863Z-d7ca6063` | 129 PASS、1 FAIL | 3 PASS | check/format PASS | 修复审计脚本目录问题后的中间版本。 |
| `20261116T072658.339021Z-c4faf25d` | **129 PASS、1 FAIL，0 errors** | **11 PASS，0 errors** | **check/format PASS** | 最终版本；总体退出码1，原因是既有失败未修复。 |

最终既有失败原文：

```text
FAILED tests/unit/test_cue_splitter.py::test_speech_fallback_gap_is_hard_boundary
assert len(cues) == 2
AssertionError: assert 1 == 2
1 failed, 129 passed
```

完整命令、耗时、各项 counts 见 `results.json.runs`。没有把该失败 xfail/skip、没有改测试断言隐藏失败。未来修复对应问题后，问题刻画应转换为新的回归要求，不能继续把旧错误行为当验收标准。

**NOT RUN：** Windows bat、Python3.12、BS-RoFormer、真实 Silero/CAM++、Qwen3-ASR、ForcedAligner推理、实际分离器/媒体流偏移、人工听校、日文识别率/漏句率/短句误删率、完整模型中断恢复、全仓库测试。不存在本轮真实音频质量 PASS 或“提升百分比”。背景音乐不被当成默认根因。

## 8. 用户本地需要验证

### A. 先重现离线基线

在项目根目录：

```bat
uv run python scripts/audit_ja_asr_stage0.py
```

每次新建 `output/ja_asr/stage0/<run-id>/`。当前预期新增11项通过，既有长停顿测试失败，所以脚本**预期非零退出1**；这是保留缺陷基线，不是整体验收通过。返回结果与本报告不同则保留该轮日志，不改断言来凑通过。

### B. 可选：保留真实视频基线

用现有工具，输出到**全新目录**，仅日文，不传 `--translate` / `--burn`；本轮代码不需要无理由清缓存或强制重提取：

```bat
bat\doraemon\04_ja_asr.bat "C:\test\temps\sample.mp4" --out-dir "D:\ja_asr_verify\stage0_run001" --verbose
```

将 sample.mp4 改为自己的固定样本，每次换新的运行编号目录。保留视频、mix/vocals、SRT、TXT、transcript、timestamp review、check/dropped（存在时）与完整终端日志；**现有输出尚不能替代 raw ASR/raw aligner 备份**。原工具的音轨缓存保持现状，不删 `_work`。

核对：

- review 的 speech_start/end 是否真是对齐边界，boundary动作后是否已变化；SRT最终时刻分别记录。
- 最后一两条 cue 的重叠/越界；长词或长行后半是否在语音结束后才显示。
- `うん/ううん/はい/いや/そう/違う/何？/えっ、嘘？` 的文本和时轴；重复附和是否被改写；没有独立语义的单音节填充音另列，不当必保留漏句。
- 哪些区间 ASR 有文本但内部漏句、哪些只在补漏检测中出现；不得用空 uncovered 当作“没有漏句”。
- 对每个真实问题记录 video + start/end + 当前文本 + 人工听到文本 + 能确认的发音区间 + 所属运行；拿不准则标记，不更改时间凑指标。

**请先本地核对上述产物，反馈实际结果并明确确认后，再开始阶段 1：时间轴问题修复。**
