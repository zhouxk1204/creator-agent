# Creator Intelligence Agent

每天自动同步指定抖音博主昨天发布的视频，完整保存元数据、视频和封面。Phase 2 起逐步接入 ASR、视觉理解、风格分析与 ContentDNA，形成可复用的创作者画像。

> **状态**：Phase 1 已实现（采集 → 下载 → ASR 转写跑通），另有一套独立的哆啦A梦日译中字幕工具链
> （TVer 下载 → 剧集信息 → 分割 → 日语 ASR → 翻译/烧录 → 二次学习）。

## Phase 1 范围

只跑通 **采集 -> 下载** 链路，不做任何 AI 分析：

- 注册抖音博主（`creator add`）
- 每天定时采集昨天发布的视频元数据
- 下载 `video.mp4` / `cover.jpg` / `metadata.json`
- SQLite 索引 + 状态机驱动，失败可断点续跑

Phase 2 才做：FunASR ASR、Qwen2.5-VL Vision、StyleAnalyzer、ContentDNA、日报。

## 技术栈

Python 3.12 · uv · Playwright (sync API, persistent context) · httpx · SQLite · Pydantic v2 · Typer · loguru · pytest · ruff

## 用法

```bash
uv sync                                          # 安装依赖
uv run playwright install chromium               # 首次安装浏览器
uv run creator-agent auth login                  # 交互式登录抖音（headful 一次）
uv run creator-agent doctor                      # 环境/磁盘/登录态自检
uv run creator-agent creator add "<博主主页 URL>"
uv run creator-agent creator list
uv run creator-agent sync                        # 同步昨天视频
uv run creator-agent sync --creator douyin_12345 --days 3
```

每日 2 点定时同步通过系统 crontab 触发（不在进程内调度）：

```bash
0 2 * * * cd /path/to/creator-agent && uv run creator-agent sync >> logs/cron.log 2>&1
```

## Windows 快捷方式（bat/ 目录）

- **`bat/douyin_download_only.bat`** —— 只下载单个抖音视频，不转录：
  - 双击运行 → 自动从剪贴板读取抖音链接下载
  - 或命令行带参数：`bat\douyin_download_only.bat "https://v.douyin.com/xxxx"`（URL 含 `&` 时必须加双引号）
- **`bat/douyin_download_transcribe.bat`** —— 抖音下载 + ASR 转文字，用法同上（需要 creator-asr 环境，见 `asr` 命令说明）
- **`bat/any_link_download_transcribe.bat`** —— 交互式一键下载 + 转写（抖音 / 小红书自动识别）：
  - 双击运行 → 提示输入链接，粘贴（右键）后回车即可；直接回车则从剪贴板读
  - 或命令行带参数：`bat\any_link_download_transcribe.bat "<链接>"`（URL 含 `&` 时必须加双引号）
  - 整段分享文案也能直接粘，会自动从中抽出链接
- **`bat/xhs_download_transcribe.bat`** —— 小红书笔记：下载视频 + ASR 转文字（见下方「小红书」一节）
- **`bat/sync_creators.bat`** —— 同步所有已注册博主的昨天视频，参数透传（如 `bat\sync_creators.bat --days 3`）
- **`bat/menu.bat`** —— 交互菜单（doctor / sync / creator list）

### 哆啦A梦流水线（`bat/doraemon/`，按执行顺序编号）

- **`01_download_tver.bat`** —— 下载 TVer 视频（默认最新一集哆啦A梦）：
  - 双击运行 → 下载最新一集到 `storage/tver/srtsxzl3si/`（含封面和 info-json）
  - `--list` 只列出在播剧集；`--all` 下载全部在播；`--episode epenb4xglc` 指定一集；`--series <id或URL>` 换其他节目
  - 原理：`service-api.tver.jp` 内部 API 列剧集 + yt-dlp 下载（无加密 HLS，最高 1080p）
  - **需要 ffmpeg**（音视频分流合并）：Windows 自动复用 `settings.yaml` 的 `asr.ffmpeg_path`；macOS 用 `brew install ffmpeg`
  - macOS/Linux 用等价的 `sh/tver.sh`（参数相同）
- **`02_fetch_doraemon.bat`** —— 抓取哆啦A梦剧集页（标题 / 简介 / 图片）+ 翻译标题简介为中文：
  - 双击运行 → 输入集数（如 `934`）；或命令行：`bat\doraemon\02_fetch_doraemon.bat 934`
  - 集数自动补零拼成 `https://www.tv-asahi.co.jp/doraemon/story/0934/`，结果存到 `storage/doraemon/0934/`（`metadata.json` + `story.md` + 图片）
  - 翻译需要本地 LLM 服务，bat 会自动检测并启动（见 `bat/start_translate_server.bat`）
  - macOS/Linux 用等价的 `sh/doraemon.sh 934`（不带参数则提示输入集数）
- **`03_split_episode.bat`** —— 把一集视频里的多个小故事切成独立单集（纯视觉检测标题封面，不用 OCR/AI）：
  - 双击运行 → 拖入视频或粘贴路径 → 先预览检测（打开 `output\preview\contact_sheet.jpg`）→ 确认 Y 才切割
  - 输出 `output\935#1.mp4` / `935#2.mp4` / `935_split.json`；`--copy` 快速模式（对齐关键帧，可能偏 1 秒级）
  - 漏检调 `--sim 0.975` / `--min-duration 3.5`，误检调 `--min-duration 5` / `--episodes 2`
- **`04_ja_asr.bat`** —— 日语视频去背景音 + ASR + 说话人分离 + 词级对齐字幕 + 翻译（需要 creator-asr-ja 环境，见下文）：
  - **双击运行 = 处理 `C:\test\temps\` 里的所有视频**：ASR → 日文 `<名字>.srt` → 中文 `<名字>.zh.srt`（各步已完成会自动跳过；默认不烧录，要烧录自己加 `--burn`）
  - 双击会自动检测并启动翻译服务（llama.cpp，见 `bat/start_translate_server.bat`），无需手动先起服务
  - 或 `bat\doraemon\04_ja_asr.bat video.mp4`（仅 ASR）/ `... --burn`（额外烧录 `.zh.mp4`），也可把 mp4 拖到 bat 上
  - 输出 `<名字>.txt` / `<名字>.srt` / `<名字>.zh.srt` / `<名字>.transcript.json`
- **`05_learn_corrections.bat`** —— 二次学习：扫描 `subtitle-project/episodes/` 下各剧集的
  `01_original_ja.srt` + `02_ai_zh.srt` + `03_final_zh.srt`，分析 AI 翻译 vs 人工修正的差异，
  沉淀到 `knowledge/` 知识库并生成 `reports/`；之后的翻译自动注入相关知识（需翻译服务在跑）

## 日语 ASR（去背景音 + 说话人分离 + Qwen3-ASR + 词级对齐字幕）

针对 TVer / 哆啦A梦等日语视频的独立工具（与 sync 流水线无关，纯文件进文件出）：

```
MP4 → ffmpeg 提取 16k WAV → audio-separator 人声分离 → silero-vad 语音段
    → CAM++ 说话人嵌入聚类 → 按说话人切换点分块（≤15s）
    → Qwen3-ASR 逐块识别 → [子进程] Qwen3-ForcedAligner 词级时间戳
    → 按标点+真实词时间切字幕（≤24 字 / ≤8s，标注 話者A/話者B）
    → <名>.txt + <名>.srt + <名>.transcript.json
```

说话人分离（CAM++）和词级对齐（Qwen3-ForcedAligner）都是可选步骤，各自独立降级：
模型没装/子进程失败只关掉自己那一步（不分人、时间轴退回按比例分摊），不影响 ASR 主流程。

### 一次性环境搭建（两个 conda 环境，与 creator-asr 的 FunASR 环境隔离）

主环境 `creator-asr-ja`（分离 / VAD / 分人 / ASR，transformers ≥5.13）：

```bash
conda create -n creator-asr-ja python=3.12 -y
conda activate creator-asr-ja
pip install torch "transformers>=5.13" accelerate audio-separator silero-vad soundfile scipy
pip install modelscope kaldiio addict datasets oss2 simplejson sortedcontainers scikit-learn torchaudio
# Windows CUDA 再装: pip install onnxruntime-gpu   (Mac 用自带 onnxruntime 即可)
```

对齐环境 `creator-asr-ja-aligner`（只跑 Qwen3-ForcedAligner）。**必须独立**：
`qwen-asr` 死锁 `transformers==4.57.6`，与主环境的 5.x 硬冲突无法共存，
所以对齐由 ja_worker 每个视频起一次子进程（`asr/aligner_worker.py`）完成：

```bat
conda create -y -n creator-asr-ja-aligner python=3.12
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install "transformers==4.57.6" accelerate librosa soundfile nagisa
pip install --no-deps qwen-asr        :: --no-deps：避免拉入 gradio/fastapi 等无关依赖
modelscope download --model Qwen/Qwen3-ForcedAligner-0.6B --local_dir C:/models/Qwen3-ForcedAligner-0.6B
```

首次运行会自动下载模型（Qwen3-ASR-1.7B ~4GB 到 `~/.cache/huggingface`，分离模型到 `~/.cache/audio-separator-models`，
CAM++ ~28MB 到 `~/.cache/modelscope`）；国内网络可设 `HF_ENDPOINT=https://hf-mirror.com`。

> ⚠️ 在这两个环境装任何包都可能把 cu128 torch 顶成 CPU 版。装完务必
> `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` 检查。

然后在 `config/settings.yaml` 里配置：

```yaml
ja_asr:
  env_python: "C:/Users/<你>/.conda/envs/creator-asr-ja/python.exe"  # macOS: ~/miniconda3/envs/creator-asr-ja/bin/python
  input_dir: "C:/test/temps"   # 视频丢进这个目录即可
  model: "Qwen/Qwen3-ASR-1.7B-hf"   # 想快速验证可换 Qwen/Qwen3-ASR-0.6B-hf
  device: "cuda:0"                  # Mac 调试用 mps / cpu
  language: "Japanese"
  # 可选：词级对齐（两个 aligner_* 必须同时配置，缺一个就退回按比例分摊时间）
  aligner_model: "C:/models/Qwen3-ForcedAligner-0.6B"
  aligner_env_python: "C:/Users/<你>/miniconda3/envs/creator-asr-ja-aligner/python.exe"
  # 可选：说话人分离（拆太碎就把 speaker_threshold 从 0.5 调大到 0.65）
  speaker_model: "iic/speech_campplus_sv_zh-cn_16k-common"
  speaker_threshold: 0.5
```

### 用法

日常用法就一步：**把视频丢进 `C:/test/temps/`，然后跑 `uv run creator-agent ja-asr`**（不带参数自动扫目录，递归，支持 mp4/mkv/mov/webm/ts/flv/avi；已有 `.srt` 的跳过，`--force` 重跑）。产物写在视频旁边。

```bash
uv run creator-agent ja-asr                            # 处理 C:/test/temps 里的新视频
uv run creator-agent ja-asr --force                    # 全部重跑
uv run creator-agent ja-asr --translate                # ASR 后接着日译中（需翻译服务，见下节）
uv run creator-agent ja-asr --translate --burn         # 再烧录成 <名>.zh.mp4
uv run creator-agent ja-asr video.mp4 [more.mp4 ...]   # 指定文件 / glob
uv run creator-agent ja-asr "storage/tver/**/*.mp4" -o out/
uv run python scripts/transcribe_ja.py                 # 等价的脚本入口
```

`--translate` 会补译存量：本轮新做 ASR 的强制重翻，已有 `.srt` 但缺 `.zh.srt` 的顺带翻译
（不覆盖已有中文字幕，保护人工修改）。

## 日译中字幕翻译 / 烧录（translate）

把日文 `.srt` 翻成中文 `.zh.srt`（**时间轴/编号逐条不变**，有单测锁定），可烧录成 `.zh.mp4`。
后端是本地 LLM，任何 OpenAI 兼容接口都行；本机用 **llama.cpp `llama-server`（Vulkan）+ Qwen3.5-9B GGUF**
（选 Vulkan 是因为本机显卡驱动 CUDA 版本与可用构建不匹配；RTX 5060 Ti 16G 跑 9B Q6_K 没问题）。

- **启动服务**：双击 `bat\start_translate_server.bat`（窗口需保持开着）；
  `translate.model` 必须等于 llama-server 的 `--alias`（`curl localhost:8080/v1/models` 可查）
- **用法**：双击 `bat\translate_subtitles.bat`（批量翻译 inbox，支持拖文件），或：

```bash
uv run creator-agent translate video.mp4          # 旁边生成 <名>.zh.srt
uv run creator-agent translate video.mp4 --force  # 覆盖重翻
uv run creator-agent translate video.mp4 --burn   # 已有 .zh.srt 直接烧录 .zh.mp4，不再调模型
```

行为约定：单条翻译失败保留日文原文并 WARNING（不中断）；批次内编号缺失自动重试→拆半→保底；
烧录中文字体默认 `Microsoft YaHei`（字幕变方块=字体名不对，`fc-list :lang=zh` 查可用名）。

## 二次学习（learn）

把人工修正后的字幕喂回来，让 LLM 当"审校专家"分析 AI 翻译 vs 人工译文的差异，沉淀成知识库；
之后每次 `translate` / `ja-asr --translate` 自动把相关术语、错误案例、翻译规则注入 prompt。

目录约定（`settings.yaml` 的 `translate.project_dir`，默认 `./subtitle-project`）：

```
subtitle-project/
├── episodes/<剧集名>/          ← 每集放 3 个输入文件
│   ├── 01_original_ja.srt      Qwen3-ASR 原始日文（ja-asr 产物）
│   ├── 02_ai_zh.srt            AI 初翻（translate 的 .zh.srt 改名）
│   ├── 03_final_zh.srt         人工修改后的最终字幕
│   └── 04_analysis.json        （learn 自动写出）
├── knowledge/                  （learn 自动维护：术语/错误案例/规则；translation_rules.md 可手工编辑）
└── reports/                    （episode_report.csv + project_report.html）
```

用法：双击 `bat\doraemon\05_learn_corrections.bat`，或 `uv run creator-agent learn`
（需翻译服务在跑）。幂等——同一集重复 learn 不会膨胀；`translation_rules.md` 手工增删下次翻译即生效。

## 剧集分割（split_episode）

命令行用法（mac / linux / 进阶；Windows 双击 `bat\doraemon\03_split_episode.bat` 即可）：

```bash
uv run python scripts/split_episode.py input/935.mp4 --preview   # 只检测，生成预览图，不切割
uv run python scripts/split_episode.py input/935.mp4             # 确认无误后真正切割
```

原理：每 0.5s 抽帧做灰度签名 → 找长时间画面高度稳定的区间（相似度 ≥0.98、持续 ≥4s、排除黑屏/纯色）
→ 候选边界 ±2s 逐帧扫描取最大不连续帧作为标题卡精确首末帧 → 第 2、3 个标题卡起始帧即分割点。
常用参数：`--episodes N`（强制集数）、`--sim`（静止判定阈值，默认 0.98）、
`--min-duration`（标题卡最短秒数，默认 4）、`--copy`（流拷贝快速切割）。

## 小红书（视频提取 + 转文案）

小红书笔记走和抖音完全相同的状态机（`NEW -> METADATA_SAVED -> VIDEO_DOWNLOADED -> ASR_DONE`），
只是入口从「博主主页」换成「单条笔记分享链接」。`creator-agent run` 会按链接自动分发到对应平台，
所以命令行和 Web UI 的粘贴框都不用区分平台。

```bash
uv run creator-agent run "https://www.xiaohongshu.com/explore/<note_id>?xsec_token=..."   # 下载 + 转写
uv run creator-agent run --no-asr "<分享链接>"                                              # 只下载
uv run creator-agent run                                                                   # 不带参数 = 读剪贴板
```

Windows 上双击 `bat\xhs_download_transcribe.bat` 即可（无参数时从剪贴板读链接）。

**怎么拿到链接**：App 里「分享 → 复制链接」，粘贴整段分享文案也行（会自动从文字里抽出链接、
丢掉 `apptime`/`track_code` 之类的统计参数，保留笔记页必需的 `xsec_token`）。

**关于登录与风控**：实测（2026-09）未登录访客就能下载；真正的拦路虎是 IP 风控（300012「安全限制」）——
连续高频访问后小红书会把页面重定向到风控页。采集器已做了两层防护：拦截风控跳转 + 直接从 SSR HTML
里解析笔记数据（不依赖页面 JS 执行）。遇到「未能从小红书笔记页解析出内容」时，等几十分钟到几小时
让风控自行解除，或切换网络；反复触发时再登录一次：

```bash
uv run creator-agent auth-login --platform xiaohongshu   # 打开有头浏览器，登录完在终端按 Enter
uv run creator-agent auth-login                          # 不带参数 = 抖音（原行为）
```

> 注意：`auth-login` 不做任何 cookie 自动检测——小红书会给未登录访客也下发 `web_session`，
> 检测它会误判。登录完成后回终端按 Enter 才会关闭浏览器。

**产物**：和抖音一致，落在 `storage/{作者昵称}/videos/{日期}/` 下 —— 视频、`cover.jpg`、
`metadata.json`、转写后的 `transcript.txt` / `transcript.json`（含逐句时间戳），音频中间文件 `*.wav`。
作者会按笔记里的 `userId` 自动注册成 `xiaohongshu_{userId}` 创作者，方便下次复用和归档。

**实现要点**：小红书笔记是服务端渲染的——整条笔记的 JSON 就内嵌在 `/explore` 文档的
`window.__INITIAL_STATE__` 里。采集器用 Playwright 导航一次，从**网络层抓取的文档正文**里
brace-match 提取这块 blob（`undefined`→`null` 后 `json.loads`），拿到标题、作者、点赞收藏分享、
话题标签、封面、时长和同 codec 多档码率的 CDN 直链（按 `size` 挑最大的一份）。不用
`page.evaluate`：风控状态下页面 JS 根本不会启动。拦截到的 `sns-video-*.xhscdn.com` mp4
响应作为兜底。视频用 httpx + cookies + Referer 直接下载（CDN 不受 IP 风控影响）。

## 架构要点

1. **插件化 Collector** —— `collector/base.py` 是抽象基类，抖音是首个实现。后续加 B 站 / YouTube 只需新增子包。
2. **平台独立** —— Collector 返回标准 `CollectedVideo`，下游不感知平台。
3. **Filter 抽象** —— Collector 不理解"昨天"，只接受 `CollectFilter(start, end, max_videos)`。
4. **状态机驱动** —— 每个 video 有 `status` 列（`NEW -> METADATA_SAVED -> VIDEO_DOWNLOADED`），失败停在最后成功状态，下次 sync 自动续跑。
5. **文件 + 索引分离** —— 媒体和 JSON 落文件系统，SQLite 只存索引与状态。
6. **部分失败不阻塞** —— 单个 video / creator 失败不影响其他。

完整接口签名、Schema、滚动策略、错误处理约定见 [CLAUDE.md](CLAUDE.md) 与 `src/creator_agent/` 源码。

## 目录结构

```
creator-agent/
├── config/settings.yaml           # 配置
├── src/creator_agent/
│   ├── models/        # Creator, CollectedVideo, Video, VideoStatus
│   ├── storage/       # FileStorage: profile.json / video.mp4 / cover.jpg / metadata.json
│   ├── repository/    # SQLite Repository
│   ├── browser/       # BrowserManager (Playwright)
│   ├── collector/     # base.py + douyin/{collector,parser,time_parser,selectors/} + xhs/
│   ├── downloader/    # httpx + cookies, Playwright 拦截兜底
│   ├── asr/           # FunASR 转写 + 日语 ASR（ja_worker/aligner_worker/cue_splitter/speaker_turns）
│   ├── translate/     # 日译中（translator/burner）+ 二次学习（alignment/memory/learn）
│   ├── pipeline/      # PipelineRunner - 状态机驱动
│   ├── scheduler/     # run_once; crontab 做定时
│   └── cli/           # Typer
├── scripts/           # 独立脚本：split_episode / download_tver / fetch_doraemon / translate_doraemon 等
├── bat/               # Windows 快捷方式（含 doraemon/ 流水线 01~05）
├── sh/                # macOS/Linux 等价脚本
├── tests/{unit,integration,e2e}/
├── storage/           # gitignore - 下载产物与 SQLite
└── logs/              # gitignore
```

## 相关文档

- [CLAUDE.md](CLAUDE.md) —— Claude Code 协作指引（架构原则与稳定契约）
