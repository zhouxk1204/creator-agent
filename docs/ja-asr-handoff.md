# 日语 ASR 工具交接文档（去背景音 + Qwen3-ASR + 字幕）

> 日期：2026-09-29（当晚更新）。状态：**真机全链路已验证通过**。2 分钟哆啦A梦测试片段（`storage/ja_inbox/doraemon_test_2min.mp4`）
> 用 0.6B 和 1.7B 模型各跑通一次，产出 `.txt` / `.srt` / `.transcript.json` 均正常，1.7B 质量明显更好
>（"点滅""キャベツ""ドラえもん"等 0.6B 听错的词都对了）。

## 功能回顾

```
MP4 → ffmpeg 提取 16k WAV → audio-separator 人声分离 → silero-vad 按静音分块
    → Qwen3-ASR-1.7B 逐块识别 → <名>.txt（逐句）+ <名>.srt（日语字幕）+ <名>.transcript.json
```

用法：视频丢进 `storage/ja_inbox/` → `uv run creator-agent ja-asr`（或双击 `bat/ja-asr.bat`）。已有 `.srt` 的跳过，`--force` 重跑。也支持指定文件/glob。

## 一、首次搭建（已完成 ✅，存档备查）

| # | 事项 | 实际结果 |
|---|---|---|
| 1 | conda 环境 | `creator-asr-ja`（py3.12）已建。torch **2.11.0+cu128**（RTX 5060 Ti 必须 cu128，CUDA 验证 True）、transformers 5.17.0、accelerate、audio-separator 0.47.0、silero-vad 6.2.3、soundfile、scipy、onnxruntime-gpu 1.30.0（CUDAExecutionProvider ✓）、**audioread**（清单漏了，audio-separator 经 librosa 依赖它，首跑报 `No module named 'audioread'`） |
| 2 | `settings.yaml` 的 `ja_asr.env_python` | 预填路径正确，无需改 |
| 3 | 模型 | 走 **ModelScope**（`creator-asr` 环境的 `modelscope.exe download`，~9MB/s）下到本地目录：`C:/models/Qwen3-ASR-0.6B-hf`、`C:/models/Qwen3-ASR-1.7B-hf`。HF 直连/镜像都慢不可用。settings 默认模型已改成本地路径。分离模型 BS-RoFormer 首跑自动下载（GitHub，需走代理）→ `~/.cache/audio-separator-models` |
| 4 | 首跑验证 | 0.6B / 1.7B 均跑通 |

**网络环境备忘**：本机 Clash 代理在 `127.0.0.1:7897`（Windows 系统代理已开，pip/curl 自动走；约 2MB/s）。
mirrors.aliyun.com 的 PyPI 镜像当时极慢（索引页 33KB/s），**pip 安装统一走官方源 + 代理**；ModelScope 直连很快。
⚠️ **大坑：装 audio-separator 会把 PyPI 版 CPU torch 顶掉 cu128 torch**（它声明依赖 torch，pip 直接换最新 CPU 版）。
修复：`pip install --force-reinstall --no-deps torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128`。
以后在这个环境装任何包前先确认不会动 torch（必要时 `--no-deps`）。

## 二、真机验证结论（原"高风险点"逐条销案）

1. **`apply_transcription_request`** ✅ transformers 5.17 签名与代码一致，`audio=` 传本地路径字符串可用（docstring 明确支持 URL/本地路径/ndarray）。
2. **audio-separator 输出文件名匹配** ✅ 默认模型输出含 "Vocals"，匹配逻辑工作正常。
3. **silero-vad API** ✅ 6.2.3 的 `load_silero_vad` / `get_speech_timestamps` 与代码一致。
4. **分离输出采样率** ✅ 重采样逻辑工作正常。
5. **device_map** ✅ `{"": "cuda:0"}` + accelerate 正常。

**真机修的两个 bug**：

1. **ffmpeg 不在 PATH**：audio-separator 初始化时会 `subprocess` 调裸 `ffmpeg -version`（PATH 查找），
   系统 PATH 没有 ffmpeg → `WinError 2`。修复：`ja_transcriber.py::_run_worker`
   把 `ja_asr.ffmpeg_path` 所在目录注入 worker 子进程 PATH（bat/CLI 任何入口都生效）。
2. **分离中间产物污染 CWD**：`separator.separate()` 默认把 `_(Vocals)_*.wav` / `_(Instrumental)_*.wav`
   写到进程当前目录（双击 bat 时 = 仓库根目录，每个视频 ~100MB）。修复：`ja_worker.py::_separate_vocals`
   每 job 把 `separator.output_dir` **和 `separator.model_instance.output_dir`** 都指到该 job 的临时目录
   （model instance 在 load_model 时就快照了 output_dir，只改 separator 的不够）；
   且 `separate()` 返回的是**裸文件名**（相对其 output_dir），需自行 join 回去再打开。

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
