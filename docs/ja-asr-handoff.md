# 日语 ASR 工具交接文档（去背景音 + 说话人分离 + Qwen3-ASR + 词级对齐字幕）

> 日期：2026-09-30。状态：**真机验证通过**（说话人分离 + 词级对齐全链路端到端跑通，
> 单测 255 全过、ruff src/+tests/ 干净）。
> 本次在旧管线（人声分离 + VAD + Qwen3-ASR）基础上新增两步：**CAM++ 说话人分离**
> 和 **Qwen3-ForcedAligner 词级对齐**，解决"字幕太长"和"两人的话并入一条字幕"两个问题。
> 旧文档（2026-09-29 版，首次搭建记录）已删除；环境搭建要点并入本文第一节。
>
> **2026-09-30 真机验证更新（重要架构变更）**：词级对齐器 `qwen-asr` **死锁
> `transformers==4.57.6`**，与主 ASR 模型需要的 `transformers>=5.13` **硬冲突、无法共存**。
> 因此对齐被拆到**独立 conda 环境 `creator-asr-ja-aligner`**，ja_worker 通过**子进程**
> （每视频一次）调用 `aligner_worker.py` 完成词级对齐。主环境 `creator-asr-ja`（5.17）
> 的 ASR 完全不动。真机还逼出并修复了 `_diarize` 的两个真 bug（见第二节）。

## 功能回顾

```
MP4 → ffmpeg 提取 16k WAV → audio-separator 人声分离 → silero-vad 语音段
    → CAM++ 说话人嵌入聚类 → 按说话人切换点分块（≤15s）
    → Qwen3-ASR-1.7B 逐块识别 → [子进程] Qwen3-ForcedAligner 词级时间戳
    → 按标点+真实词时间切字幕（≤24 字 / ≤8s，标注 話者A/話者B）
    → <名>.txt + <名>.srt + <名>.transcript.json
```

用法不变：视频丢进 `C:/test/temps/` → `uv run creator-agent ja-asr`（或 `bat/doraemon/04_ja_asr.bat`）。
已有 `.srt` 的跳过，`--force` 重跑。

**双环境**：主流程（分离/VAD/分人/ASR）跑在 `creator-asr-ja`（transformers 5.17）；
只有词级对齐跑在 `creator-asr-ja-aligner`（transformers 4.57.6），由 ja_worker 每个视频
起一次子进程调用 `aligner_worker.py`。两个可选步骤（分人 / 对齐）各自独立降级：
模型没装/子进程失败只关掉自己那一步（退回不按比例分摊时间、不分说话人），不影响 ASR
主流程；worker stderr 会打 `[ja-worker] WARNING` 说明。

## 一、环境现状（均已建好并验证，无需再装）

### 主环境 `creator-asr-ja`（transformers 5.17 + cu128 torch）

人声分离 + VAD + CAM++ 分人 + Qwen3-ASR。本次新增/补齐：`modelscope` `kaldiio` `addict`
`datasets` `oss2` `simplejson` `sortedcontainers` `scikit-learn` `torchaudio==2.11.0+cu128`。
CAM++ 模型首跑自动下载（~28MB，已下载到 `~/.cache/modelscope`）。

### 对齐环境 `creator-asr-ja-aligner`（transformers 4.57.6 + cu128 torch）

只跑 Qwen3-ForcedAligner。安装步骤（已执行，留档备查）：

```bat
conda create -y -n creator-asr-ja-aligner python=3.12
:: GPU 版 torch（与主环境一致，RTX 5060 Ti 需 cu128）
pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
pip install "transformers==4.57.6" accelerate librosa soundfile nagisa
pip install --no-deps qwen-asr        :: --no-deps：避免拉入 gradio/fastapi 等无关依赖
:: 模型（已下载到 C:/models/Qwen3-ForcedAligner-0.6B，1.84GB）
modelscope.exe download --model Qwen/Qwen3-ForcedAligner-0.6B --local_dir C:/models/Qwen3-ForcedAligner-0.6B
```

`settings.yaml`：`ja_asr.aligner_model=C:/models/Qwen3-ForcedAligner-0.6B`，
`ja_asr.aligner_env_python=C:/Users/34696/miniconda3/envs/creator-asr-ja-aligner/python.exe`，
`ja_asr.speaker_model=iic/speech_campplus_sv_zh-cn_16k-common`。**两个 aligner_* 必须同时配置**，
缺一个就退回按比例分摊时间。

> ⚠️ 老坑提醒：在这两个环境装任何包都可能把 cu128 torch 顶成 CPU 版。装完务必
> `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`，
> 不对就 `pip install --force-reinstall --no-deps torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128`。

### 验证步骤（已跑通，回归时用）

1. 回归（不开新模型）：`uv run creator-agent ja-asr --force <测试视频>`，字幕变短
   （标点切句 + 按比例分摊时间），证明 cue_splitter 链路 OK。
2. 完整链路（默认即开 aligner + speaker）：字幕时间轴贴合语音（句间静音不计入时长，
   时间戳出现非整秒精度如 `00:00:06,382`），对话处出现 `話者A:`/`話者B:` 前缀。
3. 真机已用 90s 测试片验证：分离→VAD→CAM++ 聚类→17 块 ASR→对齐子进程 17/17 成功
   →25 条字幕，全程无 WARNING。

## 二、真机验证发现 / 已解决的高风险点

| # | 点 | 真机实测结论 | 处理 |
|---|---|---|---|
| 1 | `qwen_asr.Qwen3ForcedAligner.align()` | **已确认**：返回 list，`res[0]` 可按下标取 `ForcedAlignItem`，每项 `.text/.start_time/.end_time`。时间**相对该 chunk**，`aligner_worker.py` 已加 `chunk_start` 偏移 | 无需改 |
| 2 | modelscope pipeline `output_emb=True` | **抓到两个真 bug（已修复）**：①入参必须是 **list**（`sv([path], ...)`），传裸字符串会被逐字符迭代报 `FileNotFoundError: 'C'`；②返回键是 **`embs`**（shape [N,192]），**不是** `spk_embedding`。`ja_worker._diarize` 已改 | 已修复 |
| 3 | modelscope 是否用 GPU | 未强制，默认即可；说话人模型小、每段一次前向，速度够用 | 无需改 |
| 4 | 聚类阈值 `speaker_threshold: 0.5` | 90s 测试片聚出 **6 位**说话人（A~F），其中一位（E）占绝大多数块。动漫多人场景可能属实，也可能是把一人拆多——**若觉得拆太碎，调大到 0.65** | 调 settings.yaml，不改代码 |
| 5 | transformers 5.17 与 qwen-asr 版本兼容 | **确认是 5.x 与 4.x 硬冲突**：qwen-asr 全版本（0.0.1~0.0.6）死锁 `transformers==4.57.6`，与 ASR 需要的 `>=5.13` 无法共存（import 即崩：deprecated 装饰器 + `qwen3_asr` 重复注册）。**无法靠调 qwen-asr 版本解决** → 拆独立环境 | 已拆 `creator-asr-ja-aligner` |

## 三、这次改了什么（代码侧）

| 文件 | 改动 |
|---|---|
| `asr/cue_splitter.py`（新） | 纯函数：按标点切句、超长按顿号/硬切、词级时间戳→字幕时间（无词时间时按字数比例分摊）。`max_cue_chars`/`max_cue_sec` 双上限 |
| `asr/speaker_turns.py`（新） | 纯函数：标签平滑（单点误标翻转）、按说话人分块（换人必断块）、話者A/B 命名 |
| `asr/aligner_worker.py`（新，2026-09-30） | 独立对齐环境（4.57.6）的 worker：读 `align_jobs.json`（chunk wav + text + chunk_start）→ `Qwen3ForcedAligner.align()` 逐块对齐 → 加 chunk 偏移 → sentinel 协议回传词表。每视频被子进程调一次 |
| `asr/ja_worker.py` | 新管线串联；新增 `--aligner-model / --aligner-python / --speaker-model / --speaker-threshold / --max-cue-chars / --max-cue-sec / --max-chunk`；`_diarize()` 提取 CAM++ 嵌入 + AgglomerativeClustering（cosine, average linkage）。**2026-09-30 真机修复**：①`_diarize` 给 modelscope 传 list、取 `out["embs"]`（原裸字符串 + `spk_embedding` 会崩）；②词级对齐从进程内 `_align_words` 改为 `_align_batch` **子进程**（两阶段：先全块 ASR，再一次子进程对齐，最后切字幕）；删除进程内 `_load_aligner`（主环境 5.17 无法 import qwen_asr）；max_chunk 默认 30s→15s |
| `asr/ja_transcriber.py` | 透传新配置给 worker（含 `--aligner-python`）；.txt 输出行加 `話者X:` 前缀 |
| `asr/subtitle.py` | SRT cue 加 `話者X:` 前缀；`parse_srt` 解析回 segment |
| `models/transcript.py` | `TranscriptSegment.speaker: str \| None` |
| `config.py::JaAsrSettings` + `settings.yaml` | 新增 `aligner_model / aligner_env_python / speaker_model / speaker_threshold / max_cue_chars / max_cue_sec / max_chunk_sec` |
| `tests/unit/test_{cue_splitter,speaker_turns}.py`（新） | 19 个新单测；`test_subtitle.py` 加 2 个。**2026-09-30 修**：`test_parse_srt_multiline_and_crlf` 在 Windows 因 `write_text` 文本模式二次转义 CRLF 而必挂，加 `newline=""` 修正 |

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
| stderr 有 `aligner subprocess ...` / 对齐全走按比例 | aligner 环境没建好 / `aligner_env_python` 路径错 / 模型路径错；手动复现：`C:/.../creator-asr-ja-aligner/python.exe src/creator_agent/asr/aligner_worker.py --jobs <json> --model C:/models/Qwen3-ForcedAligner-0.6B`。只影响时间轴精度 |
| stderr 有 `aligner_python not found` / `未同时配置` | `aligner_model` 和 `aligner_env_python` 没同时配 |
| stderr 有 `speaker model load failed` | modelscope 没装 / 首次下载失败；只影响分人 |
| stderr 有 `FileNotFoundError: 'C'`（分人） | 旧 bug 复发迹象：`_diarize` 必须给 modelscope 传 **list** 且取 `out["embs"]` |
| 字幕没有 話者 前缀 | `speaker_model` 是否为空；或 `_diarize` 抛异常走了降级（stderr 有 WARNING） |
| 所有人被标成同一話者 | 阈值太大→调小 `speaker_threshold`；或嵌入提取全失败 |
| 一个人被拆成多个話者 | 阈值太小→调大（90s 片 6 位即偏碎，可试 0.65） |
| 字幕时间轴和语音对不上 | aligner 子进程没跑成；确认 stderr 有 `[ja-aligner]` 进度且无 WARNING、`.srt` 时间非均匀分摊 |
| worker 报 model load 错 | 手动跑 `python src/creator_agent/asr/ja_worker.py --jobs ...` 复现（用 creator-asr-ja 的 python） |

## 六、关键文件索引

| 文件 | 角色 |
|---|---|
| `src/creator_agent/asr/ja_worker.py` | 主环境（5.17）worker：分离+VAD+分人+ASR，再以子进程调对齐；真机调试主战场 |
| `src/creator_agent/asr/aligner_worker.py` | 对齐环境（4.57.6）worker：Qwen3-ForcedAligner 词级时间戳 |
| `src/creator_agent/asr/cue_splitter.py` | 字幕切句策略纯函数（调字幕粒度改这里） |
| `src/creator_agent/asr/speaker_turns.py` | 说话人分块策略纯函数 |
| `src/creator_agent/asr/vad_chunker.py` | VAD 合并策略纯函数 |
| `src/creator_agent/asr/ja_transcriber.py` | 主环境编排 |
| `src/creator_agent/config.py::JaAsrSettings` | 配置项 |
| `tests/unit/test_{cue_splitter,speaker_turns,ja_transcriber,subtitle,vad_chunker}.py` | 单测 |

验证命令：`uv run pytest -m "not e2e"`、`uv run ruff check src/`。
