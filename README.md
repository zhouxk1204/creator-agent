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
- **`bat/sync.bat`** —— 同步所有已注册博主的昨天视频，参数透传（如 `bat\sync.bat --days 3`）
- **`bat/creator-agent.bat`** —— 交互菜单（doctor / sync / creator list）
- **`bat/doraemon.bat`** —— 抓取哆啦A梦剧集页（标题 / 简介 / 图片）：
  - 双击运行 → 输入集数（如 `934`）
  - 或命令行带参数：`bat\doraemon.bat 934`
  - 集数自动补零拼成 `https://www.tv-asahi.co.jp/doraemon/story/0934/`，结果存到 `storage/doraemon/0934/`（`metadata.json` + `story.md` + 图片）
  - macOS/Linux 用等价的 `sh/doraemon.sh 934`（不带参数则提示输入集数）

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
