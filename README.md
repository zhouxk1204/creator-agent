# Creator Intelligence Agent

每天自动同步指定抖音博主昨天发布的视频，完整保存元数据、视频和封面。Phase 2 起逐步接入 ASR、视觉理解、风格分析与 ContentDNA，形成可复用的创作者画像。

> **状态**：Phase 1 设计已定稿，代码尚未实现。完整设计见 [docs/superpowers/specs/2026-07-09-creator-intelligence-agent-phase1-design.md](docs/superpowers/specs/2026-07-09-creator-intelligence-agent-phase1-design.md)。

## Phase 1 范围

只跑通 **采集 -> 下载** 链路，不做任何 AI 分析：

- 注册抖音博主（`creator add`）
- 每天定时采集昨天发布的视频元数据
- 下载 `video.mp4` / `cover.jpg` / `metadata.json`
- SQLite 索引 + 状态机驱动，失败可断点续跑

Phase 2 才做：FunASR ASR、Qwen2.5-VL Vision、StyleAnalyzer、ContentDNA、日报。

## 技术栈

Python 3.12 · uv · Playwright (sync API, persistent context) · httpx · SQLite · Pydantic v2 · Typer · loguru · pytest · ruff

## 计划用法

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

- **`bat/download.bat`** —— 只下载单个抖音视频，不转录：
  - 双击运行 → 自动从剪贴板读取抖音链接下载
  - 或命令行带参数：`bat\download.bat "https://v.douyin.com/xxxx"`（URL 含 `&` 时必须加双引号）
- **`bat/transcribe.bat`** —— 下载 + ASR 转文字，用法同 `download.bat`（需要 creator-asr 环境，见 `asr` 命令说明）
- **`bat/run.bat`** —— 交互式一键下载 + 转写（抖音 / 小红书自动识别）：
  - 双击运行 → 提示输入链接，粘贴（右键）后回车即可；直接回车则从剪贴板读
  - 或命令行带参数：`bat\run.bat "<链接>"`（URL 含 `&` 时必须加双引号）
  - 整段分享文案也能直接粘，会自动从中抽出链接
- **`bat/xhs.bat`** —— 小红书笔记：下载视频 + ASR 转文字（见下方「小红书」一节）
- **`bat/sync.bat`** —— 同步所有已注册博主的昨天视频，参数透传（如 `bat\sync.bat --days 3`）
- **`bat/creator-agent.bat`** —— 交互菜单（doctor / sync / creator list）
- **`bat/doraemon.bat`** —— 抓取哆啦A梦剧集页（标题 / 简介 / 图片）：
  - 双击运行 → 输入集数（如 `934`）
  - 或命令行带参数：`bat\doraemon.bat 934`
  - 集数自动补零拼成 `https://www.tv-asahi.co.jp/doraemon/story/0934/`，结果存到 `storage/doraemon/0934/`（`metadata.json` + `story.md` + 图片）
  - macOS/Linux 用等价的 `sh/doraemon.sh 934`（不带参数则提示输入集数）
- **`bat/tver.bat`** —— 下载 TVer 视频（默认最新一集哆啦A梦）：
  - 双击运行 → 下载最新一集到 `storage/tver/srtsxzl3si/`（含封面和 info-json）
  - `--list` 只列出在播剧集；`--all` 下载全部在播；`--episode epenb4xglc` 指定一集；`--series <id或URL>` 换其他节目
  - 原理：`service-api.tver.jp` 内部 API 列剧集 + yt-dlp 下载（无加密 HLS，最高 1080p）
  - **需要 ffmpeg**（音视频分流合并）：Windows 自动复用 `settings.yaml` 的 `asr.ffmpeg_path`；macOS 用 `brew install ffmpeg`
  - macOS/Linux 用等价的 `sh/tver.sh`（参数相同）
- **`bat/ja-asr.bat`** —— 日语视频去背景音 + ASR + 说话人分离 + 词级对齐字幕 + 翻译（需要 creator-asr-ja 环境，见下文）：
  - **双击运行 = 处理 `storage\ja_inbox\` 里的所有视频**：ASR → 日文 `<名字>.srt` → 中文 `<名字>.zh.srt`（各步已完成会自动跳过；默认不烧录，要烧录自己加 `--burn`）
  - 双击会自动检测并启动翻译服务（llama.cpp，见 `bat/translate-server.bat`），无需手动先起服务
  - 或 `bat\ja-asr.bat video.mp4`（仅 ASR）/ `... --burn`（额外烧录 `.zh.mp4`），也可把 mp4 拖到 bat 上
  - 输出 `<名字>.txt` / `<名字>.srt` / `<名字>.zh.srt` / `<名字>.transcript.json`

## 日语 ASR（去背景音 + Qwen3-ASR + 字幕）

针对 TVer / 哆啦A梦等日语视频的独立工具（与 sync 流水线无关，纯文件进文件出）：

```
MP4 → ffmpeg 提取 16k WAV → audio-separator 人声分离 → silero-vad 按静音分块
    → Qwen3-ASR 逐块识别 → 分段 txt + transcript.json + 日语 SRT 字幕
```

字幕时间轴来自 VAD 块边界（代码里预留了 Qwen3-ForcedAligner 精对齐的接口，后续可升级）。

### 一次性环境搭建（专用 conda 环境，与 creator-asr 的 FunASR 环境隔离）

```bash
conda create -n creator-asr-ja python=3.12 -y
conda activate creator-asr-ja
pip install torch "transformers>=5.13" accelerate audio-separator silero-vad soundfile scipy
# Windows CUDA 再装: pip install onnxruntime-gpu   (Mac 用自带 onnxruntime 即可)
```

首次运行会自动下载模型（Qwen3-ASR-1.7B ~4GB 到 `~/.cache/huggingface`，分离模型到 `~/.cache/audio-separator-models`）；国内网络可设 `HF_ENDPOINT=https://hf-mirror.com`。

然后在 `config/settings.yaml` 里配置：

```yaml
ja_asr:
  env_python: "C:/Users/<你>/.conda/envs/creator-asr-ja/python.exe"  # macOS: ~/miniconda3/envs/creator-asr-ja/bin/python
  input_dir: "./storage/ja_inbox"   # 视频丢进这个目录即可
  model: "Qwen/Qwen3-ASR-1.7B-hf"   # 想快速验证可换 Qwen/Qwen3-ASR-0.6B-hf
  device: "cuda:0"                  # Mac 调试用 mps / cpu
  language: "Japanese"
```

### 用法

日常用法就一步：**把视频丢进 `storage/ja_inbox/`，然后跑 `uv run creator-agent ja-asr`**（不带参数自动扫目录，递归，支持 mp4/mkv/mov/webm/ts/flv/avi；已有 `.srt` 的跳过，`--force` 重跑）。产物写在视频旁边。

```bash
uv run creator-agent ja-asr                            # 处理 ja_inbox 里的新视频
uv run creator-agent ja-asr --force                    # 全部重跑
uv run creator-agent ja-asr video.mp4 [more.mp4 ...]   # 指定文件 / glob
uv run creator-agent ja-asr "storage/tver/**/*.mp4" -o out/
uv run python scripts/transcribe_ja.py                 # 等价的脚本入口
```

## 小红书（视频提取 + 转文案）

小红书笔记走和抖音完全相同的状态机（`NEW -> METADATA_SAVED -> VIDEO_DOWNLOADED -> ASR_DONE`），
只是入口从「博主主页」换成「单条笔记分享链接」。`creator-agent run` 会按链接自动分发到对应平台，
所以命令行和 Web UI 的粘贴框都不用区分平台。

```bash
uv run creator-agent run "https://www.xiaohongshu.com/explore/<note_id>?xsec_token=..."   # 下载 + 转写
uv run creator-agent run --no-asr "<分享链接>"                                              # 只下载
uv run creator-agent run                                                                   # 不带参数 = 读剪贴板
```

Windows 上双击 `bat\xhs.bat` 即可（无参数时从剪贴板读链接）。

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

完整接口签名、Schema、滚动策略、错误处理见设计文档。

## 目录结构（规划）

```
creator-agent/
├── docs/superpowers/specs/        # 设计文档
├── config/settings.yaml           # 配置
├── src/creator_agent/
│   ├── models/        # Creator, CollectedVideo, Video, VideoStatus
│   ├── storage/       # FileStorage: profile.json / video.mp4 / cover.jpg / metadata.json
│   ├── repository/    # SQLite Repository
│   ├── browser/       # BrowserManager (Playwright)
│   ├── collector/     # base.py + douyin/{collector,parser,time_parser,selectors/}
│   ├── downloader/    # httpx + cookies, Playwright 拦截兜底
│   ├── pipeline/      # PipelineRunner - 状态机驱动
│   ├── scheduler/     # run_once; crontab 做定时
│   └── cli/           # Typer
├── tests/{unit,integration,e2e}/
├── storage/           # gitignore - 下载产物与 SQLite
└── logs/              # gitignore
```

## 相关文档

- [Phase 1 设计文档](docs/superpowers/specs/2026-07-09-creator-intelligence-agent-phase1-design.md) —— 唯一事实源
- [CLAUDE.md](CLAUDE.md) —— Claude Code 协作指引
