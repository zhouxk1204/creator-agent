# 04_ja_asr 日文 ASR 管线实现详解

本文档描述 `04_ja_asr.bat` 背后六步管线的完整实现，供核对与调试用。
源码位置：`src/creator_agent/asr/`（主流程）、`src/creator_agent/cli/main.py`（`ja-asr` 命令）、
`src/creator_agent/config.py`（`JaAsrSettings`）。

---

## 1. 总体架构：三个 Python 进程

```
04_ja_asr.bat
 └─ .venv (主 uv env, 无 torch)
     └─ creator-agent ja-asr  (cli/main.py: ja_asr)
         └─ JaTranscriber (asr/ja_transcriber.py)
             ├─ extract_audio()            ffmpeg → 16kHz 单声道 WAV
             ├─ [子进程 1] ja_worker.py    conda env: creator-asr-ja
             │     ├─ BS-RoFormer 人声分离 (audio-separator)
             │     ├─ silero-VAD × 2 遍（宽松 + 紧凑）
             │     ├─ CAM++ 说话人分离（可选）
             │     ├─ Qwen3-ASR-1.7B 逐块识别（JA-Anime-Galgame 微调版）
             │     ├─ 补漏 pass（可选）：无字幕语音区间追加一轮 ASR
             │     ├─ [子进程 2] aligner_worker.py  conda env: creator-asr-ja-aligner
             │     │     └─ Qwen3-ForcedAligner-0.6B 词级对齐（初始+补漏 chunk 同一次运行）
             │     ├─ 对齐质量门 → cue 切分 → 幻觉 cue 过滤（被丢 cue 收集进结果）
             │     └─ 打印 ===ASR_RESULTS_BEGIN=== JSON ===ASR_RESULTS_END===
             │           （ok 结果含 segments + dropped + uncovered 字段）
             ├─ boundary_check（主环境）   ffprobe + silencedetect 边界吸附
             └─ 写 .srt / .txt / .transcript.json / .check.txt / .dropped.txt
```

**为什么要三个 env：**
- `creator-asr-ja`：Qwen3-ASR 需要 `transformers>=5.13`。
- `creator-asr-ja-aligner`：`qwen-asr`（ForcedAligner）钉死 `transformers==4.57.6`，两者不能共存。
- 主 uv env：不放任何重依赖，负责音频提取、worker 调度、边界检查、写产物、翻译/烧录编排。

**绝不能与翻译 LLM server 同时跑**：llama-server + torch/CUDA + 人声分离 + ASR 模型
一起会超 Windows commit 上限，ASR 模型加载死于 `os error 1455`。所以 CLI 在 ASR 全部
结束后才惰性启动翻译 server，译完即停（commit `fe55190`）。自己手动起的 server 不会被停。

---

## 2. 六步管线逐步实现

### 步骤 1：视频提取音频（主环境，`asr/audio_extractor.py`）

- ffmpeg 命令：`-vn -acodec pcm_s16le -ar 16000 -ac 1` → 16kHz 单声道 WAV。
- **幂等缓存**：写 sidecar `<out>.wav.src.json`（源文件 size + mtime_ns）。重跑时只有源视频
  未变才复用缓存——同名替换的视频不会错用旧音频（否则整条时间轴都错）。`--force` 总是重提。
- 输出位置：`ja_asr.work_dir`（配了则持久保留）或临时目录（跑完即删）。
- ffmpeg 路径：config 的 `ffmpeg_path` 是完整构建，不在系统 PATH；worker 子进程启动时把
  ffmpeg 所在目录注入 `PATH`（audio-separator 内部会 shell 调 `ffmpeg`）。
- stderr 以 UTF-8 + errors="replace" 解码——中文文件名在 GBK 代码页下会让 stderr 读取线程
  抛 UnicodeDecodeError（提取本身成功，只是噪音）。

### 步骤 2：分离纯人声（worker env，`ja_worker._separate_vocals`）

- audio-separator + BS-RoFormer（默认 vocal 模型，可用 `--sep-model` 换）。
- **输出重定向**：audio-separator 默认把 `<name>_(Vocals)_*.wav` 写进进程 CWD。实现里把
  `separator.output_dir` 和 `separator.model_instance.output_dir` 都指向 per-job 临时目录
  （模型实例在 `load_model` 时已捕获 output_dir，两个都要改）。
- 返回的是裸文件名（相对 output_dir），需用临时目录 resolve。
- 分出的 vocal stem（通常 44.1kHz）重采样为 16kHz 单声道（`scipy.signal.resample_poly`）。
- 其余 stem（如伴奏）仅在 keep 目录开启时保留，供人耳核对分离质量。
- **后续所有步骤（VAD / ASR / 对齐）都跑在 vocal stem 上**——共享同一音频源、同一时间轴。

### 步骤 3：VAD + 分块（worker env，`vad_chunker.py` / `speaker_turns.py`）

**两遍 silero-VAD，参数不同、用途不同：**

| pass | 参数 | 用途 |
|---|---|---|
| 宽松（config 可调） | threshold=`vad_threshold`(默认 0.5), min_speech=120ms, min_silence=300ms, pad=150ms | 分块。低 min_speech 保住「あっ」「えっ」式感叹词；pad 保住弱起音不被切掉 |
| 紧凑（硬编码） | threshold=0.5（**固定**）, min_speech=100ms, min_silence=150ms, pad=30ms | ① cue 审计（丢弃不覆盖语音的字幕）② 对齐失败时的 speech-aware 回退时间轴 ③ 补漏检测基准。它是度量基准，threshold 永远不随宽松 pass 动 |

`vad_threshold` 调低（如 0.35）可找回弱语音，代价是音乐/噪音误检增多。

**分块（无说话人时，`build_chunks`）**：目标 3–8s，硬上限 15s。
1. gap ≤ 0.3s 的相邻语音段合并为 utterance group（句内换气）。
2. 相邻 group 打包进同一 chunk，直到跨度超 target_max；过短的 chunk（< target_min）允许
   撑过 target_max 直到 hard_max 来凑够长度。
3. 单 group 超 hard_max：在内部停顿最接近 target_max 处递归切（`forced_split_natural`）；
   单条连续语音段超 hard_max 且无停顿可切：硬切（`forced_split_hard`）。
4. 每 chunk 两侧 pad 0.1s，clamp 到 [0, duration]。

每个 chunk 带 `reason`（normal / merged / forced_split_natural / forced_split_hard），
进日志和 SUMMARY 计数。

**说话人分离（可选，`speaker_model` 配置后启用）：**
- CAM++（ModelScope）对每个 ≥0.4s 的语音段提取 192 维嵌入（短段继承邻居标签）。
- AgglomerativeClustering（cosine, average linkage, distance_threshold=speaker_threshold=0.5；
  阈值越低说话人越多）。
- `smooth_labels`：两轮把两侧同标签的孤立误标翻转（短附和「うん」嵌入常不稳定）。
- `build_speaker_chunks`：说话人切换必开新 chunk——一条字幕绝不混两个人的话。
  显示名为 話者A/B/C…（按首次出现顺序，全片稳定）。
- 失败只降级为普通分块，不影响整个批次。

### 步骤 4：Qwen3-ASR 逐块识别（worker env）

- 模型当前部署为 `jaykwok/Qwen3-ASR-1.7B-JA-Anime-Galgame-hf`（Qwen3-ASR-1.7B 的日语
  动漫/Galgame 微调，本地 `C:/models/Qwen3-ASR-1.7B-JA-Anime-Galgame-hf`；官方原版
  `Qwen/Qwen3-ASR-1.7B-hf` 在 settings.yaml 里留注释可回滚，0.6B 可提速），
  `apply_transcription_request` + generate，
  `max_new_tokens=1024`（512 会截长 chunk）。
- **幻觉防护 `cut_repeat_tail`**：音乐/噪音上模型会循环同一单元（「バイバイ。バイバイ。…」）。
  结尾同一单元重复 ≥3 次且跨度 ≥6 字符 → 砍回只留一次。**只砍尾巴**——句首结巴（「ねねねね…」）
  是真实语音不动。截断发生在对齐之前，免得幽灵词把真实 cue 拖离音频。
  设 `repeat_tail_max_cps`（如 12.0）后加**语速判据**：只有 len(text)/该块语音时长（紧凑 VAD）
  超过此值才砍——幻觉循环产出的文本远超音频能容纳的量，而正常语速的「うんうんうん」会保留。
  0 = 旧行为（一律砍）。砍时日志带 `cps=` 可直接核对判据。
- 每块打日志：`[CHUNK nnn/NNN][ASR] OK 1.2s len=18 text="…"`；空文本 / 异常分别计
  `asr_empty` / `asr_failed`，跳过该块继续。

### 步骤 4.5：补漏 pass（`recover_missed` 开启时）

第一轮 ASR 完成后、**调起 aligner 子进程之前**执行（补漏 chunk 和初始 chunk 走同一次
aligner 运行，不重复加载模型）：

1. 覆盖代理 = 所有成功 ASR chunk 的跨度（speech-aware 时间轴下，成功 chunk 内的紧凑 VAD
   语音必然被某条 cue 覆盖，chunk 跨度是精确代理，无需等真实 cue）。
2. `subtract_intervals(recovery, covered, recover_min_sec)` → 有语音但没人识别的区间；
   间隔 ≤0.6s 的相邻段先 `merge_close` 合并（否则 0.3s 双侧 pad 的 ASR 窗口会重叠重复识别）。
   这里的 `recovery` 是**独立的补漏检测 pass**：默认 = `tight`（threshold 0.5），把
   `recover_vad_threshold` 调低（如 0.35）可找回主分块阈值漏掉的**弱/远语音行**（告别、远处
   喊话）。关键：只调这个补漏阈值，**别动主分块 `vad_threshold`**——实测把主阈值降到 0.3 会把
   间隔桥接成超长 `forced_split_hard` 块、ASR 幻觉反而更糟；低补漏阈值只追加小补漏块，不动主分块。
   补漏 chunk 的 cue 时间轴回退与幻觉过滤也改用这个低阈值 pass（否则弱语音行会被当成幻觉丢掉）。
3. 每段生成 pad 0.3s 的补漏 chunk（`reason="recovered"`，进 SUMMARY 计数 `chunk_recovered`），
   走同一个 `_asr_pass`（ASR + repeat-tail 防护），随后与初始 chunk 一起对齐、过质量门、
   切 cue、**同样过幻觉过滤**；与已有 cue 的微小重叠交给 `resolve_overlaps`。
4. 补漏后仍有语音无字幕的区间 → 结果 JSON 的 `uncovered` 字段 → `.check.txt` 里的
   `[UNRECOVERED_SPEECH]` 人工听清单。

日志：`[ja-worker][vid] 补漏检测：N 段有语音无字幕` + 每段 `[CHUNK nnn][RECOVER] candidate …`。

注意：第一轮 ASR 出了文本但最终 cue 全被幻觉过滤丢掉的区间**不会**重试（直接进
UNRECOVERED 清单）——这是刻意的，重试同样的音频大概率还是幻觉。

### 步骤 5：词级时间对齐（aligner env 子进程）

- **每视频起一个 aligner 子进程**（模型加载约半分钟），逐块流式返回：每对完一块立刻在
  stdout 打一行 `===ALIGN_CHUNK=== {json}`，ja_worker 读一行处理一块（质量门 + cue 切分流水线化，
  不等全片对完）；stderr 线程实时转发。
- 对齐窗口 = chunk 范围两侧各加 `align_pad_sec`（默认 0.3s）——宽到能找回被 VAD 切掉的起音，
  窄到对齐器几乎看不到相邻语句、不会把重复词锁到隔壁句上。词时间由 aligner_worker 加上
  `chunk_start` 换算成绝对秒。
- aligner 异常/缺失只降级该 chunk，绝不中断批次。

**先修复再过门（`align_check.repair_word_timing`）**：ForcedAligner 经常在一组本来很准的
词时间里夹几个**零时长 token**（「は」「の」「っ」「じゃ」等短功能 mora 对不齐就塌缩成
start==end），偶尔还有相邻词毫秒级重叠。这些无害（词就在正确位置、夹在放准的词之间），
但若直接整 chunk 拒掉，14/19 的块会被迫退回比例分摊——这正是早期字幕时间轴整体漂移的根因。
所以过门前先修复：零/负时长词扩到 0.05s、≤0.1s 的小重叠前向钳齐。只有**真坏**才返回 None
交回退：有真实时长的词回跳超过 0.1s（锁到相邻语句），或超过一半的词都要修（整条塌缩、
不比比例分摊强）。修复不改变词文本，故覆盖率 / 出窗判定不受影响。

**再过对齐质量门（`align_check.check_alignment`，五项按序检查，首败即拒）：**

| 常量 | 含义 |
|---|---|
| `NO_WORD_TIMESTAMPS` | 对齐器没返回任何词 |
| `LOW_COVERAGE` | ASR 文本匹配到词的字符占比 < `align_min_coverage`（0.5） |
| `OUT_OF_WINDOW` | 词超出 [chunk_start−pad, chunk_end+pad] |
| `INVALID_TIMING` | start ≥ end，或非单调递增（修复后仍如此，或修复返回 None） |
| `ABNORMAL_DURATION` | 词时长不在 [0.02s, 3.0s] |

被拒的对齐**带原因回退**到 speech-aware 比例分摊（绝不静默），计数进 SUMMARY 的
`align_fallback_<REASON>`。子进程提前死掉没返回的块记 `SUBPROCESS`，对齐器内部异常记
`EXCEPTION`。

**为什么只小修、不无条件信**：对齐器可能返回貌似合理的垃圾（词锁到相邻语句）。旧实现用中点
对称去重会同时弄坏两条 cue；现在是「小修零时长/微重叠 + 大回跳/大面积塌缩仍拒」，
`resolve_overlaps` 只做微小真实边缘重叠的最后保险。

### 步骤 5 收尾（worker 内）：cue 切分（`cue_splitter.split_cues`）

> 对应 bat 头注释第 3–4 步的后处理：每个 ASR chunk 对齐完（或回退后）立刻切成字幕 cue。

一个 ASR chunk 可能含好几句，切成 ≤24 字 / ≤8s 的字幕 cue：

1. **硬边界**：词间（或 speech 区间间）静音 ≥ `pause_sec`（0.6s）→ cue 绝不跨越；
   跨停顿的句子在停顿处切开。
2. **句末标点**（。！？!?）先切；相邻短句在 max_chars / max_sec 预算内重新合并（不跨停顿）。
3. 仍超长的句子在软标点（、，,…・：:）靠近上限处换行，无软标点则硬切 max_chars。
4. 仍超 max_sec 的 cue 在靠近中点处递归强制切。
5. 最终强制单调、不重叠、非空。

**三种时间轴（fallback 顺序）：**
- **word_timed**：对齐词序列拼接通常能还原 ASR 文本（但无标点）。逐字走查把每个文本字符
  映射到产生它的词（`match_words_to_text`，质量门的 coverage 检查共享同一实现，两边度量一致）。
  cue 起点 = 首词 start，cue 终点 = 末词 end（词间静音不归属任何 cue）。要求 ≥50% 字符匹配，
  否则降级。词计时 cue 允许越出 chunk 边界（对齐窗口本来就 pad 过）。
- **speech-aware 比例分摊**（对齐被拒时）：文本按字符占比摊到**压缩后的纯语音时间轴**上
  （chunk 内紧凑 VAD 区间拼接、挤掉静音）。cue 永远起止于语句边缘、不覆盖句间静音。
- **plain linear**（无 speech 信息兜底）：[start, end] 线性插值，clamp 在 chunk 内。

**幻觉 cue 过滤**：cue 在紧凑 VAD 下的语音覆盖率 < 0.3 → 丢弃并日志
（`[CHUNK nnn][CUE] DROP speech_cov=…`）。真实对话几乎不会低于这个地板。
被丢的 cue 同时收集进结果 JSON 的 `dropped` 字段，主环境写成 `<stem>.dropped.txt`
（格式 `[12.34-14.10] speech_cov=0.21  文本`）——事后可核对丢掉的是幻觉还是真台词。

### 步骤 6（主环境，bat 第 5+6 步）：边界检查 + 写产物（`boundary_check.py`）

worker 只见过 vocal stem，从没人拿原始媒体校验它的时间轴——本步在主环境补上
（只依赖 ffmpeg/ffprobe，worker 退出后、临时音频清理前跑）：

1. **`probe_video_duration`**：ffprobe 原视频容器时长，与提取音频时长对比。音频短于视频
   >1s → `AUDIO_VIDEO_MISMATCH`（提取可能被截断，整条时间轴都偏短）。
2. **`detect_silences`**：ffmpeg `silencedetect=noise=-35dB:d=0.12` 跑在 **vocal stem** 上
   （伴奏里的音乐可能掩盖停顿；vocal 不可用时回退到混音 WAV）→ 真实静音区间。
   结尾没收到 `silence_end` 的开口静音在 duration 处闭合。
3. **`refine_cues`**（纯函数，逐边独立）：
   - cue 边落在静音 (s,e) 内 → 吸附到语音边缘：start → e − lead_in(0.08s)，
     end → s + tail_out(0.10s)。移动量 ≤ snap_window(0.5s) 算**可信修正**（不记可疑）；
     深静音里吸附距离超窗 → 保留原时间并记 `START_IN_SILENCE` / `END_IN_SILENCE`。
   - cue 边落在语音中间是**正常**的（句内标点切分），只在 snap_window 内有静音边时顺手抛光，
     从不记可疑。
   - 语音覆盖率 < 0.3 → `NO_SPEECH`（幻觉/错对齐）。
   - end 超视频时长（容差 0.05s）→ `EXCEEDS_VIDEO`（clamp）。
   - 最后重排为单调、不重叠、≥0.2s。
4. 报告写 `<stem>.check.txt`；**.srt 本身保持干净**（要导入剪映核对时间轴）。
   边界检查失败只跳过该视频（保留原 segments），绝不让视频失败。
5. 随后 `_write_outputs` 写产物：`.srt` 过 `finalize_for_srt`（配了才变），worker 结果里的
   `dropped` → `.dropped.txt`，`uncovered` 已并入上面的 check 报告（`[UNRECOVERED_SPEECH]`）。
   注意：**边界检查关闭时没有 .check.txt**，uncovered 清单只出现在 SUMMARY 日志里。

---

## 3. 产物布局

输出目录 = `--out-dir` 或 `ja_asr.out_dir`（vault 的 episodes/）；每个视频一个子目录：

```
<out_dir>/<stem>/
├── <stem>.srt              # 日文字幕（干净，导入剪映核对时间轴；可配显示时间 polish）
├── <stem>.txt              # 一段一行（带 話者X 前缀），方便阅读/diff
├── <stem>.transcript.json  # Transcript 模型全量（含 segments 真实语音时间）
├── <stem>.check.txt        # 边界检查报告（吸附数、可疑 cue、UNRECOVERED_SPEECH 清单）
├── <stem>.dropped.txt      # 被幻觉过滤丢弃的 cue（仅当有丢弃时生成）
└── <stem>.review.<时间戳>.json  # 听片标注文件：cue（id 与 srt 序号一致）+ 诊断 + question/expected
```

**review.<时间戳>.json（听片标注，`review_json: true` 默认开）**：对着 .srt 听片时发现问题，
直接在对应 `id` 的条目里填 `question`（现象）/ `expected`（可选，正确台词）；每条 cue
自带诊断（chunk、对齐回退原因、speech_cov、repeat_tail_cut、boundary），`dropped` /
`uncovered` 条目也可标注——反馈格式见 `docs/subtitle-issue-template.md`。
**每次运行生成一个新的带时间戳文件，旧的永远保留**——重跑（--force 调参）不会动
你填过标注的文件；注意 cue 序号随参数变化，旧文件的标注只对当次运行的 cue 有意义，
对照最新 .srt 时取时间戳最新的那份。`meta.overrides` 记录当次运行的调参值，
不同实验的结果不会搞混。

**SRT 显示时间 polish**（`finalize_for_srt`，只作用在 .srt 上，transcript.json/.txt 保持
真实语音时间）：`srt_min_display_sec` 把过短的 cue 拉到可读时长（不越过下一条 cue 起点 −20ms、
不越媒体时长）；`srt_lead_in_sec`/`srt_tail_sec` 加提前/延后余量——边界检查开启时这两个
**保持 0**（吸附已含 lead_in/tail_out，再开会重复加）。三项全 0 = 关闭（旧行为）。

`ja_asr.work_dir` 配置后，中间产物持久保留（默认 `C:\test\temps\_work`）：

```
<work_dir>/
├── <vid>.wav               # 提取的 16k 混音 + <vid>.wav.src.json（缓存校验 sidecar）
├── <vid>.vocals.wav        # 分离出的人声
└── <vid>.instrumental.wav  # 伴奏（核对分离质量用）
```

**跳过逻辑**：目标目录已有 `<stem>.srt` 即视为完成；`--force` 忽略已有 SRT 和音频缓存全量重跑。

---

## 4. 配置项速查（`config/settings.yaml` 的 `ja_asr:` 段）

**调参文件 `config/settings.validate.yaml`**：`ja-asr` 启动时自动加载（存在即应用），
只覆盖 `ja_asr` 段——环境路径、模型、设备仍看主配置。调参流程：改一行 → `--force` 重跑 →
`--out-dir` 按实验分目录对比。文件里所有值 = 当前默认，原样跑 = baseline；改名/移走即回
baseline。`--config <文件>` 可指定其他实验配置；无法识别的键会打印警告并忽略。
CLI 的 `--language/--device/--model/-v` 优先级高于调参文件。

> 所有实验开关（`vad_threshold`/`recover_missed`/`repeat_tail_max_cps`/`srt_*`）默认保持
> **旧行为**——不改配置跑出的结果与改动前逐字节一致，方便和 baseline 目录逐项对比。
> 建议一次只开一项：① `vad_threshold: 0.35` → ② `recover_missed: true` →
> ③ `repeat_tail_max_cps: 12.0` → ④ `srt_min_display_sec: 0.7`。每项重点看 SUMMARY 的
> `speech not subtitled` 总时长、`dropped (no speech)` 数量、`chunk_recovered` 条数，
> 以及 `.dropped.txt` 里被丢的是不是真台词。

| 键 | 默认 | 说明 |
|---|---|---|
| `env_python` | "" | **必填**。creator-asr-ja env 的 python.exe |
| `input_dir` | `C:/test/temps` | 无参运行时扫描此目录（递归） |
| `out_dir` | "" | 输出根目录；空 = 写到视频旁边 |
| `work_dir` | "" | 中间产物保留目录；空 = 跑完即删 |
| `model` | `jaykwok/Qwen3-ASR-1.7B-JA-Anime-Galgame-hf` | 当前部署（日语动漫/Galgame 微调）；代码默认官方 `Qwen/Qwen3-ASR-1.7B-hf`，可换 0.6B 提速 |
| `sep_model` | "" | audio-separator 模型文件名；空 = 默认 BS-RoFormer |
| `device` | `cuda:0` | `mps` / `cpu` 用于 Mac 调试 |
| `language` | `Japanese` | |
| `aligner_model` | "" | ForcedAligner HF id 或本地路径 |
| `aligner_env_python` | "" | creator-asr-ja-aligner env 的 python。**两者都配**才启用词级对齐，否则 cue 时间按比例分摊 |
| `speaker_model` | "" | ModelScope CAM++ id；空 = 不做说话人分离 |
| `speaker_threshold` | 0.5 | 聚类 cosine 距离阈值，越低说话人越多 |
| `max_cue_chars` / `max_cue_sec` | 30 / 5.0 | cue 字数/时长上限（30 按人工修正字幕行长调） |
| `pause_sec` | 0.6 | 词间静音 ≥ 此值 = 硬 cue 边界 |
| `vad_min_speech_ms` / `vad_min_silence_ms` / `vad_speech_pad_ms` | 120 / 300 / 150 | 宽松 VAD（分块用） |
| `vad_threshold` | 0.5 | 宽松 pass 概率阈值；**别低于 ~0.4**（实测 0.3 桥接成超长块、ASR 更糟）。弱语音行走 `recover_vad_threshold`。紧凑 pass 固定 0.5 不动 |
| `recover_missed` / `recover_min_sec` | true / 0.4 | 补漏 pass 开关；短于此的漏检区间不补 |
| `recover_vad_threshold` | 0.35 | 补漏**检测**阈值（独立于主分块阈值）：调低找回弱/远语音行；补漏 chunk 的时间轴回退 + 幻觉过滤同用此阈值 |
| `repeat_tail_max_cps` | 0.0 | >0 启用语速判据（建议试 12.0）：字/秒 ≤ 此值的真实重复（「うんうん」）不砍；0 = 一律砍 |
| `srt_min_display_sec` / `srt_lead_in_sec` / `srt_tail_sec` | 0.0 / 0.0 / 0.0 | .srt 显示时间 polish。边界检查开启时 lead/tail 保持 0 避免重复加余量 |
| `chunk_target_min_sec` / `chunk_target_max_sec` / `max_chunk_sec` | 3.0 / 8.0 / 15.0 | 分块目标区间 + 硬上限 |
| `asr_max_new_tokens` | 1024 | ASR 生成上限 |
| `align_pad_sec` / `align_min_coverage` | 0.3 / 0.5 | 对齐窗口 padding（也是词允许越界的最远距离）/ 质量门覆盖率下限 |
| `boundary_check` | true | 边界检查总开关 |
| `boundary_snap_sec` | 0.5 | cue 边允许移动去够静音的最大距离 |
| `boundary_silence_db` / `boundary_min_silence_sec` | -35.0 / 0.12 | silencedetect 噪声门限 / 最短静音 |
| `boundary_lead_in_sec` / `boundary_tail_sec` | 0.08 / 0.10 | 字幕比语音早出现/晚消失的余量 |
| `verbose` | false | worker 调试日志（原始 VAD 段、词时间、cue 明细） |

---

## 5. 调试指南

### 5.1 日志契约（grep 速查）

worker 的所有进度走 **stderr**（主进程实时转发）；stdout 只放结果 JSON。

- `[ja-asr] …` — 主环境编排（提取、worker 启停、边界检查、写产物）
- `[ja-worker][<vid>] …` — 视频级步骤（分离 → VAD → 说话人 → 分块 → ASR → 对齐）
- ` 42%|████▏   | 115/273 [01:32<02:11,  1.20it/s]` — 人声分离的 tqdm 进度行，
  每 5 秒一行（worker 把 audio-separator 的 `\r` 重绘改成了换行 + 节流，
  否则主进程按行转发 stderr 时分离阶段会"假死"几分钟，没有任何输出）
- `[CHUNK nnn/NNN][STAGE] …` — 块级流水线，**grep `CHUNK 005` 可重建一个块走 VAD→ASR→ALIGN→CUE 的完整路径**
- `[ja-aligner] …` — aligner 子进程（加载、每块对齐耗时）
- `ASR SUMMARY` — 每视频收尾计数器（见 5.2）
- `===ASR_RESULTS_BEGIN===` / `===ALIGN_CHUNK===` — 机器可读契约，勿改

### 5.2 用 SUMMARY 定位坏集出在哪一层

```
VAD      segments / short / long      → 段异常多/少 → 调 VAD 参数或检查分离质量
Chunks   total + reason 分布          → forced_split_hard 多 = VAD 把音乐当语音
         （含 recovered = 补漏 chunk 数，recover_missed 开启时）
ASR      success / empty / failed / repeat-tail cut
         → repeat-tail cut 多 = 幻觉严重（分离不干净或音乐段）；日志带 cps= 核对判据
Alignment success / fallback 按原因分
         → OUT_OF_WINDOW 多 = 词锁到相邻语句（align_pad 太大或块太密）
         → LOW_COVERAGE 多 = ASR 文本与对齐词对不上
         → SUBPROCESS = aligner 进程死了（看 [ja-aligner] 报错）
Subtitle cues / dropped (no speech)   → dropped 多 = 幻觉 cue 被过滤（好事）或时间轴整体漂移（坏事）
Coverage speech not subtitled         → 有语音没字幕的区间列表，长区间 = 丢台词
```

### 5.3 逐层排查路径

1. **字幕整体偏移/缺失** → 先看 `.check.txt` 的 `AUDIO_VIDEO_MISMATCH`（音频提取被截断）。
2. **某段没字幕** → SUMMARY 的 "speech not subtitled" 区间 + `.check.txt` 的
   `[UNRECOVERED_SPEECH]` 清单；配合 `[CHUNK …][CUE] DROP` 日志和 `.dropped.txt`
   看是被过滤还是 ASR 没出字（`asr_empty`）。确认是真台词被漏 → 开 `recover_missed`
   或调低 `vad_threshold` 重跑（`--force`）。
2b. **怀疑幻觉过滤误杀真台词** → 翻 `.dropped.txt`：`speech_cov` 接近 0.3 且文本像正常
   台词的要人工听原片确认；cov 极低（<0.1）的基本是音乐/噪音幻觉，过滤正确。
3. **字幕时间与语音对不上** → 看该 chunk 的 `[ALIGN]` 行：fallback 原因是什么。
   `-v/--verbose` 会打出原始 VAD 段、每个词的时间、每条 cue 的明细。
4. **人声分离质量存疑** → 听 `work_dir` 里的 `.vocals.wav` / `.instrumental.wav`。
5. **两个说话人混在一条字幕** → 确认 `speaker_model` 已配；误分可微调 `speaker_threshold`
   （调低 = 拆出更多说话人）。
6. **os error 1455 / ASR 模型加载失败** → 翻译 LLM server 和 ASR 同时跑了。CLI 已保证顺序
   （ASR 完才起 server）；手动起 server 时别同时跑 04。
7. **翻译 502 Bad Gateway** → 已修（commit `bc286bd`）：请求绕过系统代理 + 5xx 重试。

### 5.4 手动跑 worker 复现

worker 是独立脚本，可脱离 CLI 单跑（jobs.json = `[{"video_id","audio_path"}]`，音频需 16k 单声道）：

```bat
<creator-asr-ja>\python.exe src\creator_agent\asr\ja_worker.py --jobs jobs.json ^
    --aligner-model <ForcedAligner路径> --aligner-python <creator-asr-ja-aligner>\python.exe -v
```

结果 JSON 在 stdout 的 `===ASR_RESULTS_BEGIN/END===` 之间；单视频失败只置 `ok:false`，
退出码恒为 0（调用方读 JSON 判成败）。ok 结果字段：

```json
{"video_id": "...", "ok": true, "text": "...", "duration_sec": 1430.5,
 "segments": [{"start": 1.2, "end": 3.4, "text": "…", "speaker": "話者A"}],
 "vocals_path": ".../ep01.vocals.wav",
 "dropped":   [{"start": 12.3, "end": 14.1, "text": "…", "speech_cov": 0.21, "chunk": 5}],
 "uncovered": [[65.2, 68.9]]}
```

---

## 6. 已知边界与约定

- **可选步骤只降级不失败**：对齐器不可用 → 比例分摊；说话人模型加载失败 → 不做分离；
  边界检查失败 → 保留原 segments。单视频失败不阻塞批次。
- **模型缓存**：HF → `~/.cache/huggingface`（镜像设 `HF_ENDPOINT`）；分离模型 →
  `~/.cache/audio-separator-models`；ModelScope（说话人）→ `~/.cache/modelscope`。
- **Windows 编码**：子进程强制 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`；pipe 读取用
  bytes + `errors="replace"`（GBK 代码页安全）。stdout/stderr 分线程读取防 Windows 小管道死锁。
- `--translate` / `--burn` 串在同一命令尾部：ASR 全结束后惰性启动翻译 server，新转写的强制
  重翻（force=True），已有 SRT 只补缺的中文（force=False，保住手工修改）。最后按
  `translate.export_subtitles` 把字幕镜像进 vault。
