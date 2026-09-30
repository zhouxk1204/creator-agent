# 日语 ASR 工具交接文档（去背景音 + 说话人分离 + Qwen3-ASR + 词级对齐字幕）

> 日期：2026-09-30。状态：**代码已实现、单测全过（210 个），等真机装模型验证**。
> 本次在旧管线（人声分离 + VAD + Qwen3-ASR）基础上新增两步：**CAM++ 说话人分离**
> 和 **Qwen3-ForcedAligner 词级对齐**，解决"字幕太长"和"两人的话并入一条字幕"两个问题。
> 旧文档（2026-09-29 版，首次搭建记录）已删除；环境搭建要点并入本文第一节。

## 功能回顾

```
MP4 → ffmpeg 提取 16k WAV → audio-separator 人声分离 → silero-vad 语音段
    → CAM++ 说话人嵌入聚类 → 按说话人切换点分块（≤15s）
    → Qwen3-ASR-1.7B 逐块识别 → Qwen3-ForcedAligner 词级时间戳
    → 按标点+真实词时间切字幕（≤24 字 / ≤8s，标注 話者A/話者B）
    → <名>.txt + <名>.srt + <名>.transcript.json
```

用法不变：视频丢进 `storage/ja_inbox/` → `uv run creator-agent ja-asr`（或 `bat/ja-asr.bat`）。
已有 `.srt` 的跳过，`--force` 重跑。

两个新步骤都是**可选降级**：模型没装/加载失败只关掉自己那一步（退回按比例分摊时间、
不分说话人），不影响 ASR 主流程；worker stderr 会打 `[ja_worker] WARNING` 说明。

## 一、真机要做的事（模型与依赖）

以下全部在 Windows 真机的 `creator-asr-ja` conda 环境里做（**不是**主 uv 环境）。

### 1. ForcedAligner（词级时间戳）

```bat
:: 装 qwen-asr 包（注意先用 pip show torch 确认不会动 cu128 torch，必要时 --no-deps）
pip install -U qwen-asr
:: 模型走 ModelScope 下载（~9MB/s），与 ASR 模型同目录
modelscope.exe download --model Qwen/Qwen3-ForcedAligner-0.6B --local_dir C:/models/Qwen3-ForcedAligner-0.6B
```

`settings.yaml` 的 `ja_asr.aligner_model` 已预填 `C:/models/Qwen3-ForcedAligner-0.6B`。

### 2. CAM++ 说话人模型

```bat
pip install modelscope kaldiio scikit-learn
:: 模型不用手动下，modelscope pipeline 首跑自动拉（~几十MB）
```

`settings.yaml` 的 `ja_asr.speaker_model` 已预填 `iic/speech_campplus_sv_zh-cn_16k-common`。

> ⚠️ 老坑提醒：在这个环境装任何包都可能把 cu128 torch 顶成 CPU 版。装完务必
> `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`，
> 不对就 `pip install --force-reinstall --no-deps torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128`。

### 3. 验证步骤（按顺序）

1. 不带新模型先跑通回归：`uv run creator-agent ja-asr --force <测试视频>`，
   字幕应该已经变短（标点切句 + 按比例分摊时间），证明 cue_splitter 链路 OK。
2. 加 aligner：`aligner_model` 已填则默认启用。看 stderr 没有 aligner WARNING，
   字幕时间轴应明显贴合语音（句间静音不再算进字幕时长）。
3. 加 speaker：字幕应出现 `話者A:` / `話者B:` 前缀，对话轮替处一定分条。
4. 完整重跑哆啦A梦测试片，对照画面抽查 3~5 处双人对话。

## 二、真机验证要点 / 高风险点（代码未在真机跑过的部分）

| # | 点 | 不确定处 | 排障 |
|---|---|---|---|
| 1 | `qwen_asr.Qwen3ForcedAligner.align()` | 返回 `results[0]` 的词列表，每项 `.text/.start_time/.end_time`（模型卡用法）。时间是**相对该 chunk** 的，代码已加 chunk 偏移 | 手动跑 `python -c` 调 align 打印结构 |
| 2 | modelscope pipeline `output_emb=True` | 返回 dict 的 `spk_embedding` 键（192 维）。若键名变了，看 `TypeError/KeyError` 后打印 out 的 keys | `sv(str(p), output_emb=True)` 手动试 |
| 3 | modelscope 是否用 GPU | pipeline 默认可能跑 CPU。说话人模型小，CPU 也够（每段一次前向），不强制 | 慢再查 `pipeline(..., device='gpu')` |
| 4 | 聚类阈值 `speaker_threshold: 0.5` | 动漫角色音色差异大，0.5 应该够；若两人被并成一人→调小到 0.35；若一人被拆成多人→调大到 0.65 | 改 settings.yaml 即可，不用改代码 |
| 5 | transformers 5.17 与 qwen-asr 包版本兼容 | qwen-asr 是较新的包，可能要求更新 transformers | 按报错升/降 qwen-asr 版本 |

## 三、这次改了什么（代码侧）

| 文件 | 改动 |
|---|---|
| `asr/cue_splitter.py`（新） | 纯函数：按标点切句、超长按顿号/硬切、词级时间戳→字幕时间（无词时间时按字数比例分摊）。`max_cue_chars`/`max_cue_sec` 双上限 |
| `asr/speaker_turns.py`（新） | 纯函数：标签平滑（单点误标翻转）、按说话人分块（换人必断块）、話者A/B 命名 |
| `asr/ja_worker.py` | 新管线串联；新增 `--aligner-model / --speaker-model / --speaker-threshold / --max-cue-chars / --max-cue-sec / --max-chunk`；`_diarize()` 提取 CAM++ 嵌入 + AgglomerativeClustering（cosine, average linkage）；可选模型加载失败只降级；旧的 `refine_timestamps` 空接缝已删除（对齐已实现）；max_chunk 默认 30s→15s |
| `asr/ja_transcriber.py` | 透传新配置给 worker；.txt 输出行加 `話者X:` 前缀 |
| `asr/subtitle.py` | SRT cue 加 `話者X:` 前缀 |
| `models/transcript.py` | `TranscriptSegment.speaker: str \| None` |
| `config.py::JaAsrSettings` + `settings.yaml` | 新增 `aligner_model / speaker_model / speaker_threshold / max_cue_chars / max_cue_sec / max_chunk_sec` |
| `tests/unit/test_{cue_splitter,speaker_turns}.py`（新） | 19 个新单测；`test_subtitle.py` 加 2 个 |

**进度显示（2026-09-30 追加）**：worker 从阻塞式 `subprocess.run` 改成 `Popen`，stderr 实时转发到
控制台。跑批时能看到每个阶段的进展：`[ja-asr] 提取音频 2/5` → `[ja-worker] 加载模型` →
`[ja-worker][视频名] 人声分离中/完成(耗时)` → `VAD N 个语音段` → `说话人嵌入 i/N + 聚类 M 位` →
`块 i/N（耗时）：[話者X] k 条字幕｜识别文本预览` → `完成：N 条字幕`。卡在哪一步、哪一步慢，
看最后一条进度行即可定位。worker 的 stderr 尾部（50 行）在非零退出时也会带进异常信息。

## 四、已知设计限制

- **aligner 单段上限 5 分钟**：chunk 已 ≤15s，不会触顶。
- **短于 0.4s 的语音段不提嵌入**（「うん」「はい」之类），直接继承邻居标签 + 平滑，
  短回应偶尔标错人是预期内的。
- **说话人是匿名标签**（話者A/B），不做"这是哆啦A梦"的角色实名映射。
- **CAM++ 是中文数据训练的**：跨语言说话人嵌入一般可用，但动漫夸张音色（机械音、
  变声道具回）可能聚类不稳，以真机效果为准，先调阈值再考虑换模型。
- **与 sync 流水线完全隔离**：不进 SQLite、不动 VideoStatus。独立工具定位不变。
- **`keep_vocals: true` 只在同时指定 `--out-dir` 时生效**。

## 五、排障速查

| 症状 | 看哪里 |
|---|---|
| stderr 有 `aligner load failed` | qwen-asr 没装 / 模型路径错；只影响时间轴精度 |
| stderr 有 `speaker model load failed` | modelscope 没装 / 首次下载失败；只影响分人 |
| 字幕没有 話者 前缀 | `speaker_model` 是否为空；或 `_diarize` 抛异常走了降级（stderr 有 WARNING） |
| 所有人被标成同一話者 | 阈值太大→调小 `speaker_threshold`；或嵌入提取全失败 |
| 一个人被拆成多个話者 | 阈值太小→调大 |
| 字幕时间轴和语音对不上 | aligner 没启用或失败；确认 stderr 无 WARNING 且 `.srt` 时间不是均匀分摊 |
| worker 报 model load 错 | 手动跑 `python src/creator_agent/asr/ja_worker.py --jobs ...` 复现 |

## 六、关键文件索引

| 文件 | 角色 |
|---|---|
| `src/creator_agent/asr/ja_worker.py` | 专用环境 worker（分离+VAD+分人+ASR+对齐），真机调试主战场 |
| `src/creator_agent/asr/cue_splitter.py` | 字幕切句策略纯函数（调字幕粒度改这里） |
| `src/creator_agent/asr/speaker_turns.py` | 说话人分块策略纯函数 |
| `src/creator_agent/asr/vad_chunker.py` | VAD 合并策略纯函数 |
| `src/creator_agent/asr/ja_transcriber.py` | 主环境编排 |
| `src/creator_agent/config.py::JaAsrSettings` | 配置项 |
| `tests/unit/test_{cue_splitter,speaker_turns,ja_transcriber,subtitle,vad_chunker}.py` | 单测 |

验证命令：`uv run pytest -m "not e2e"`、`uv run ruff check src/`。
