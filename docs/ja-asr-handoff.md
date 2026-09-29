# 日语 ASR 工具交接文档（去背景音 + Qwen3-ASR + 字幕）

> 日期：2026-09-29。状态：**代码完成、单测全绿（140 passed），但 worker 全链路一次都没在真机跑过**。
> 所有改动尚未提交（`git status` 可见 4 个修改 + 9 个新文件）。

## 功能回顾

```
MP4 → ffmpeg 提取 16k WAV → audio-separator 人声分离 → silero-vad 按静音分块
    → Qwen3-ASR-1.7B 逐块识别 → <名>.txt（逐句）+ <名>.srt（日语字幕）+ <名>.transcript.json
```

用法：视频丢进 `storage/ja_inbox/` → `uv run creator-agent ja-asr`（或双击 `bat/ja-asr.bat`）。已有 `.srt` 的跳过，`--force` 重跑。也支持指定文件/glob。

## 一、回家必做（首次搭建）

| # | 事项 | 说明 |
|---|---|---|
| 1 | 建 conda 环境 | `conda create -n creator-asr-ja python=3.12 -y`，然后 `pip install torch "transformers>=5.13" accelerate audio-separator silero-vad soundfile scipy onnxruntime-gpu`（CUDA 版 torch 按显卡选对应 cu 源） |
| 2 | 核对 `config/settings.yaml` 的 `ja_asr.env_python` | 已预填 `C:/Users/34696/miniconda3/envs/creator-asr-ja/python.exe`，若 conda 安装路径不同需改 |
| 3 | 首次运行下载模型 | Qwen3-ASR-1.7B（~4GB → `~/.cache/huggingface`）+ 分离模型（→ `~/.cache/audio-separator-models`）。网络慢先 `set HF_ENDPOINT=https://hf-mirror.com`（Windows）|
| 4 | 拿一集短视频首跑验证 | 建议先 `--model Qwen/Qwen3-ASR-0.6B-hf` 快速跑通，再切回 1.7B |

## 二、未验证的高风险点（首跑最可能挂的地方）

代码是按官方文档/API 惯例写的，以下点**没有真机验证过**，按可能性排序：

1. **Qwen3-ASR 的 `apply_transcription_request` 调用**（`ja_worker.py::_transcribe_chunk`）
   - 按模型卡示例写的（`audio=<本地wav路径>, language="Japanese"`，`.to(model.device, model.dtype)`，`processor.decode(..., return_format="transcription_only")`）。
   - 若 transformers 5.x 实际签名不同（参数名、decode 返回值结构），**只改这一个函数**即可。
2. **audio-separator 输出文件名匹配**（`ja_worker.py::_separate_vocals`）
   - 假设输出文件名含 "vocals"（如 `xxx_(Vocals)_model.wav`）。若默认模型输出命名不同，按实际打印的 `outputs` 列表调整匹配逻辑。
3. **silero-vad API 版本差异**（`ja_worker.py::_speech_chunks`）
   - 用的是 `from silero_vad import load_silero_vad, get_speech_timestamps`，返回 `[{"start": 样本序号, "end": ...}]`（按样本数除以 16000 得秒）。旧版包 API 不同（torch.hub 方式），装最新版即可。
4. **audio-separator 的输出采样率**（`ja_worker.py::_to_16k_mono`）
   - 假设可能输出 44.1kHz 立体声，已做重采样+混单声道（依赖 scipy，audio-separator 自带）。若它直接输出 16k 也没问题（有判断）。
5. **device_map 写法**（`ja_worker.py::_load_asr`）
   - `device_map={"": device}` 需要 accelerate；`mps`/`cpu` 路径在 Mac 上只做了冒烟（未跑模型）。

## 三、已知设计限制（有意为之，但要知道）

- **字幕时间轴 = VAD 块边界**，不是词级精对齐。密集对白时一个块可能到 30 秒上限，对应一行超长字幕。备选改进：
  - 调小 `vad_chunker.merge_speech_intervals` 的 `max_chunk`（如 10~15s）；
  - 或实现 `ja_worker.py::refine_timestamps`（已留接缝）接 Qwen3-ForcedAligner-0.6B（词级对齐；注意它单段上限 5 分钟、日语需 `pip install nagisa`）。
- **SRT 未做按字数/时长的 cue 拆分**（日语一行 30 秒对白会很长）。需要时在 `subtitle.py::segments_to_srt` 前加一个 split 步骤。
- **`keep_vocals: true` 只在同时指定 `--out-dir` 时生效**（`ja_transcriber.py:148`），vocals 写到 out_dir 下 `<名>.vocals.wav`。不指定 out-dir 时静默丢弃。
- **与 sync 流水线完全隔离**：不进 SQLite、不动 VideoStatus、web UI 看不到。这是按需求定的（独立工具）。
- **重复文件名处理**：不同目录下同 stem 的视频会加 `_2` 后缀区分 job id，但产物仍按原 stem 写回各自目录，互不覆盖。

## 四、明确未实现（后续需求候选）

| 事项 | 备注 |
|---|---|
| e2e 测试 | 计划里有 `@pytest.mark.e2e` 真机测试，未写。参考 `tests/e2e/test_real_asr.py` 的模式 |
| ForcedAligner 精对齐 | 接缝已留（见上） |
| 中日双语字幕 | 仓库根目录的 `fix_subtitles.py` 是早前手工对轴脚本，暗示有翻译需求；本工具只出日语原文 |
| 集成进 pipeline / web UI | 若以后想让 sync 下来的抖音视频也走 Qwen3-ASR，把 `JaTranscriber` 像 `Transcriber` 一样挂进 `PipelineRunner` 即可 |
| 批量进度展示 | worker 子进程是阻塞式 `subprocess.run`，长批次时只能看 worker 自己 print 的日志（没被实时转发）。需要的话改成 Popen + 逐行转发 stderr |

## 五、排障速查

| 症状 | 看哪里 |
|---|---|
| `JA ASR is not configured` | `settings.yaml` 的 `ja_asr.env_python` 路径 |
| worker 报 model load 错 | stderr 会原样带出（transcriber 截取最后 1500 字符抛出）；先手动跑 `python src/creator_agent/asr/ja_worker.py --jobs ...` 复现 |
| 分离结果没有人声轨 | `_separate_vocals` 的 outputs 打印，调匹配规则 |
| 字幕时间轴整体偏移 | 不会偏移（时间戳直接来自原音频采样位置）；块太粗见"设计限制"第 1 条 |
| 识别出中文/乱语 | `--language Japanese` 是否传进 worker（CLI 有 `-l` 覆盖） |

## 六、关键文件索引

| 文件 | 角色 |
|---|---|
| `src/creator_agent/asr/ja_worker.py` | 专用环境 worker（分离+VAD+ASR），真机调试主战场 |
| `src/creator_agent/asr/ja_transcriber.py` | 主环境编排 + `scan_videos`/`split_pending`（inbox 扫描、增量跳过） |
| `src/creator_agent/asr/vad_chunker.py` | 分块策略纯函数（调字幕粒度改这里） |
| `src/creator_agent/asr/subtitle.py` | SRT 生成 |
| `src/creator_agent/config.py::JaAsrSettings` | 配置项 |
| `src/creator_agent/cli/main.py::ja_asr` | CLI 入口 |
| `scripts/transcribe_ja.py` / `bat/ja-asr.bat` | 等价入口 |
| `tests/unit/test_{ja_transcriber,subtitle,vad_chunker}.py` | 22 个单测 |

验证命令：`uv run pytest -m "not e2e"`、`uv run ruff check src/ scripts/transcribe_ja.py tests/`（注意仓库根目录 `fix_subtitles.py`、`temp_patch_final.py` 等存量文件本身有 lint 错误，与本次改动无关）。
