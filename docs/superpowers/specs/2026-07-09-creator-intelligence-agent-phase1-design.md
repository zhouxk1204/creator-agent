# Creator Intelligence Agent - Phase 1 Design

**Date**: 2026-07-09
**Status**: Design approved, ready for implementation plan
**Phase**: Phase 1 (Collection-only MVP)

## Goal

每天自动同步指定抖音博主昨天发布的视频，并完整保存元数据。Phase 1 不做任何 AI 分析，只跑通采集->下载链路。

## Scope

### Phase 1 包含

- Task 1: 工程初始化（uv, src layout, Typer CLI, loguru, pydantic-settings）
- Task 2: 领域模型（Creator, CollectedVideo, Video, VideoStats, VideoAsset, VideoStatus）
- Task 3: 文件存储（profile.json, metadata.json, video.mp4, cover.jpg）
- Task 4: SQLite Repository（creator, video with status, sync_history）
- Task 5: Playwright BrowserManager
- Task 6: DouyinCollector（Filter 抽象 + 状态机）
- Task 7: Downloader（video.mp4, cover.jpg, metadata.json）
- 最小 Scheduler（run_once + 系统 crontab）
- 最小 CLI（creator add/remove/list, sync, doctor, auth login）

### Phase 1 跳过（Phase 2 再做）

- Task 8: FunASR ASR
- Task 9: Qwen2.5-VL Vision
- Task 10: StyleAnalyzer
- Task 13: Report 日报
- Task 14: ContentDNA Engine
- analysis 表、KNOWLEDGE 聚合
- 进程内定时调度（APScheduler）

## Tech Stack

- Python 3.12, uv 管理依赖
- Playwright（sync API, persistent context）
- httpx（视频/封面下载）
- static-ffmpeg pip 包（FFmpeg 依赖,Phase 2 才用,Phase 1 仅安装）
- SQLite（标准库 sqlite3）
- Pydantic v2, pydantic-settings
- Typer（CLI）
- Loguru（日志）
- pytest（测试）
- ruff（lint + format）

## Architecture Principles

1. **插件化**：`collector/base.py` 定义抽象基类，DouyinCollector 是实现。后续加 B站/Youtube 只需新增 collector 子包。
2. **平台独立**：Collector 返回标准 `CollectedVideo`，下游不感知平台。
3. **Filter 抽象**：Collector 不理解"昨天"，只接受 `CollectFilter(start, end, max_videos)`。
4. **状态机驱动**：每个 video 有 `status` 列，失败可断点续跑。
5. **文件 + 索引分离**：JSON/媒体在文件系统，SQLite 只存索引和状态。
6. **部分失败不阻塞**：单个 video/creator 失败不影响其他。

## Project Structure

```
creator-agent/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── config/
│   └── settings.yaml
├── src/
│   └── creator_agent/
│       ├── __init__.py
│       ├── config.py
│       ├── models/
│       │   ├── __init__.py
│       │   ├── creator.py
│       │   └── video.py         # CollectedVideo, Video, VideoStats, VideoAsset, VideoStatus
│       ├── storage/
│       │   ├── __init__.py
│       │   └── file_storage.py
│       ├── repository/
│       │   ├── __init__.py
│       │   └── sqlite_repo.py
│       ├── browser/
│       │   ├── __init__.py
│       │   └── manager.py
│       ├── collector/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   └── douyin/
│       │       ├── __init__.py
│       │       ├── collector.py
│       │       ├── parser.py
│       │       ├── time_parser.py
│       │       └── selectors/
│       │           ├── __init__.py
│       │           ├── profile.py
│       │           ├── video.py
│       │           └── common.py
│       ├── downloader/
│       │   ├── __init__.py
│       │   └── downloader.py
│       ├── pipeline/
│       │   ├── __init__.py
│       │   └── runner.py
│       ├── scheduler/
│       │   ├── __init__.py
│       │   └── scheduler.py
│       └── cli/
│           ├── __init__.py
│           └── main.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── storage/                   # gitignore
└── logs/                      # gitignore
```

## Domain Models

### `models/creator.py`

```python
class Creator(BaseModel):
    id: str                      # "{platform}_{uid}",如 "douyin_12345"
    platform: Literal["douyin","bilibili","youtube"]
    platform_uid: str
    nickname: str
    homepage_url: HttpUrl
    avatar_url: HttpUrl | None = None
    bio: str | None = None
    added_at: datetime
    last_synced_at: datetime | None = None
    sync_status: Literal["idle","syncing","failed","ok","partial"] = "idle"
    last_error: str | None = None
```

### `models/video.py`

```python
class VideoStats(BaseModel):
    likes: int = 0
    comments: int = 0
    favorites: int = 0
    shares: int = 0
    views: int | None = None

class CollectedVideo(BaseModel):
    """Collector 产出的原始数据,不含内部 ID/creator_id。"""
    platform: str
    platform_vid: str
    title: str
    description: str = ""
    video_url: HttpUrl | None = None
    cover_url: HttpUrl | None = None
    published_at: datetime
    stats: VideoStats = VideoStats()
    hashtags: list[str] = []

class VideoStatus(str, Enum):
    NEW              = "NEW"
    METADATA_SAVED   = "METADATA_SAVED"
    VIDEO_DOWNLOADED = "VIDEO_DOWNLOADED"   # Phase 1 终态
    # Phase 2: ASR_DONE, VISION_DONE, STYLE_DONE, DNA_DONE, KNOWLEDGE_DONE

class Video(BaseModel):
    id: str                      # "{platform}_{vid}"
    creator_id: str
    platform: str
    platform_vid: str
    title: str
    description: str = ""
    cover_url: HttpUrl | None = None
    video_url: HttpUrl | None = None
    published_at: datetime
    duration_sec: int | None = None
    stats: VideoStats = VideoStats()
    tags: list[str] = []
    collected_at: datetime
    status: VideoStatus = VideoStatus.NEW

class VideoAsset(BaseModel):
    """运行时组合视图,不单独建表。"""
    video: Video
    video_path: Path | None = None
    cover_path: Path | None = None
    metadata_path: Path | None = None
```

## Storage & Repository

### 文件存储布局

```
storage/
└── {creator_id}/
    ├── profile.json
    ├── avatar.jpg
    └── videos/
        └── {video_id}/
            ├── video.mp4
            ├── cover.jpg
            └── metadata.json
```

### `storage/file_storage.py` 接口

```python
class FileStorage:
    def __init__(self, base_dir: Path): ...

    def save_creator_profile(self, creator: Creator) -> Path
    def load_creator_profile(self, creator_id: str) -> Creator | None
    def save_avatar(self, creator_id: str, data: bytes) -> Path

    def save_video_file(self, creator_id: str, video_id: str, data: bytes) -> Path
    def save_cover(self, creator_id: str, video_id: str, data: bytes) -> Path
    def save_metadata(self, creator_id: str, video: Video) -> Path

    def load_metadata(self, creator_id: str, video_id: str) -> Video | None
    def list_videos(self, creator_id: str) -> list[str]
    def video_exists(self, creator_id: str, video_id: str) -> bool
```

### SQLite Schema

```sql
CREATE TABLE creator (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    platform_uid TEXT NOT NULL,
    nickname TEXT NOT NULL,
    homepage_url TEXT NOT NULL,
    added_at TEXT NOT NULL,
    last_synced_at TEXT,
    sync_status TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    UNIQUE(platform, platform_uid)
);

CREATE TABLE video (
    id TEXT PRIMARY KEY,
    creator_id TEXT NOT NULL REFERENCES creator(id),
    platform TEXT NOT NULL,
    platform_vid TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    storage_path TEXT NOT NULL,
    UNIQUE(platform, platform_vid)
);

CREATE TABLE sync_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id TEXT NOT NULL REFERENCES creator(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    videos_collected INTEGER DEFAULT 0,
    videos_downloaded INTEGER DEFAULT 0,
    error TEXT
);

CREATE INDEX idx_video_creator ON video(creator_id);
CREATE INDEX idx_video_status ON video(status);
CREATE INDEX idx_video_published ON video(published_at DESC);
```

### `repository/sqlite_repo.py` 接口

```python
class Repository:
    def __init__(self, db_path: Path): ...

    # Creator
    def add_creator(self, creator: Creator) -> None
    def get_creator(self, creator_id: str) -> Creator | None
    def list_creators() -> list[Creator]
    def update_creator_sync(self, creator_id: str, status: str, synced_at: datetime, error: str | None = None) -> None
    def remove_creator(self, creator_id: str) -> None

    # Video
    def upsert_collected(self, creator: Creator, cv: CollectedVideo) -> Video
    def get_video(self, video_id: str) -> Video | None
    def list_videos_by_status(self, statuses: list[VideoStatus]) -> list[Video]
    def list_videos(self, creator_id: str | None = None, since: datetime | None = None) -> list[Video]
    def advance_status(self, video_id: str, new_status: VideoStatus) -> None

    # Sync history
    def start_sync(self, creator_id: str) -> int
    def finish_sync(self, sync_id: int, status: str, videos_collected: int, videos_downloaded: int, error: str | None = None) -> None
```

## Browser Manager

```python
class BrowserConfig(BaseModel):
    user_data_dir: Path
    headless: bool = True
    channel: str = "chromium"
    viewport: tuple[int,int] = (1280, 720)
    locale: str = "zh-CN"

class BrowserManager:
    def __init__(self, config: BrowserConfig): ...
    def start(self) -> None
    def new_page(self) -> Page
    def get_cookies(self, domain: str) -> list[dict]
    def close(self) -> None
    def __enter__(self) -> "BrowserManager"
    def __exit__(self, *exc): ...
```

**登录策略**：专用 Playwright profile（`storage/.browser_profile/`），不复用系统 Chrome。首次 `auth login` 以 `headless=False` 手动登录，后续 `headless=True` 复用 session。用 sync Playwright API。

## Collector

### Filter 抽象

```python
class CollectFilter(BaseModel):
    start: datetime
    end: datetime
    max_videos: int | None = None

def yesterday_filter(now: datetime | None = None) -> CollectFilter: ...
def last_n_days_filter(n: int, now: datetime | None = None) -> CollectFilter: ...
def latest_n_filter(n: int) -> CollectFilter: ...
```

### BaseCollector

```python
class Collector(ABC):
    platform: str

    @abstractmethod
    def collect(
        self,
        creator: Creator,
        filter: CollectFilter,
        browser: BrowserManager,
    ) -> list[CollectedVideo]:
        """
        采集 publish_time 在 [filter.start, filter.end) 内的视频。
        滚动策略:只要当前页有 >= start 的视频就继续;连续多页全部 < start 才停。
        """
```

### DouyinCollector 滚动逻辑

```python
def collect(self, creator, filter, browser):
    page = browser.new_page()
    page.goto(creator.homepage_url)
    results: list[CollectedVideo] = []
    seen_vids: set[str] = set()
    consecutive_out_of_window = 0

    while True:
        cards = parse_visible_cards(page)
        if not cards:
            break

        any_in_window = False
        for card in cards:
            if card.vid in seen_vids:
                continue
            seen_vids.add(card.vid)
            if card.published_at >= filter.end:
                continue                          # 比窗口新,跳过
            if card.published_at < filter.start:
                consecutive_out_of_window += 1
                continue
            consecutive_out_of_window = 0
            any_in_window = True
            results.append(card)

        if filter.max_videos and len(results) >= filter.max_videos:
            break
        if not any_in_window and consecutive_out_of_window >= OUT_OF_WINDOW_PAGE_THRESHOLD:
            break

        scroll_down(page)
        time.sleep(random.uniform(1, 3))

    return results
```

`OUT_OF_WINDOW_PAGE_THRESHOLD = 2`。

### Selectors 拆分

```
collector/douyin/selectors/
├── profile.py     # nickname, avatar, bio
├── video.py       # title, cover, stats, publish_time
└── common.py      # container, loading
```

### 时间解析

`time_parser.py` 解析抖音相对时间（"2小时前"/"昨天"/"3天前"/"2024-01-01"）为 UTC datetime。

## Downloader

```python
class Downloader:
    def __init__(self, storage: FileStorage, browser: BrowserManager): ...

    def download_video(self, creator: Creator, video: Video) -> Path:
        """优先 httpx + browser cookies;失败回退 Playwright 拦截响应。"""

    def download_cover(self, creator: Creator, video: Video) -> Path:
        """httpx GET。"""

    def save_metadata(self, creator: Creator, video: Video) -> Path:
        """Video.model_dump_json(indent=2) -> metadata.json。"""
```

**下载策略**：
1. httpx + cookies（从 BrowserManager 取）- 快，支持断点续传
2. Playwright 拦截 `.mp4` 响应 - 慢但可靠，作为 fallback

## Pipeline Runner（状态机驱动）

```python
class PipelineRunner:
    def __init__(self, repo, storage, browser, collector, downloader): ...

    def sync_creator(self, creator: Creator, filter: CollectFilter) -> SyncResult:
        sync_id = repo.start_sync(creator.id)
        collected = collector.collect(creator, filter, browser)
        downloaded = 0
        failed: list[tuple[str, str]] = []

        for cv in collected:
            video = repo.upsert_collected(creator, cv)
            if video.status == VideoStatus.VIDEO_DOWNLOADED:
                continue

            try:
                if video.status == VideoStatus.NEW:
                    self.downloader.save_metadata(creator, video)
                    repo.advance_status(video.id, VideoStatus.METADATA_SAVED)
                if video.status in (VideoStatus.NEW, VideoStatus.METADATA_SAVED):
                    self.downloader.download_video(creator, video)
                    self.downloader.download_cover(creator, video)
                    repo.advance_status(video.id, VideoStatus.VIDEO_DOWNLOADED)
                    downloaded += 1
            except Exception as e:
                logger.error(f"video {video.id} failed: {e}")
                failed.append((video.id, str(e)))

        repo.finish_sync(sync_id, "ok" if not failed else "partial",
                         len(collected), downloaded,
                         error="\n".join(f"{vid}: {err}" for vid, err in failed) or None)
        return SyncResult(collected=len(collected), downloaded=downloaded, failed=failed)
```

**断点续跑**：每个 video 独立处理，失败时 status 停在最后成功状态，下次 sync 自动续。

## Scheduler

```python
class Scheduler:
    def __init__(self, runner: PipelineRunner, repo: Repository): ...

    def run_once(self, filter: CollectFilter | None = None):
        f = filter or yesterday_filter()
        for creator in repo.list_creators():
            try:
                runner.sync_creator(creator, f)
            except Exception as e:
                logger.error(f"creator {creator.id} sync failed: {e}")
```

**每日 2 点定时**：系统 crontab（不在进程内调度）：
```bash
0 2 * * * cd /path/to/creator-agent && uv run creator-agent sync >> logs/cron.log 2>&1
```

## CLI

```python
app = Typer()
creator_app = Typer()
app.add_typer(creator_app, name="creator")

@creator_app.command("add")
def creator_add(homepage_url: str, nickname: str | None = None): ...

@creator_app.command("remove")
def creator_remove(creator_id: str): ...

@creator_app.command("list")
def creator_list(): ...

@app.command()
def sync(creator_id: str | None = None, days: int = 1): ...

@app.command()
def doctor(): ...

@app.command()
def auth_login(): ...
```

**使用**：
```bash
creator-agent auth login
creator-agent doctor
creator-agent creator add "https://www.douyin.com/user/xxx"
creator-agent creator list
creator-agent sync
creator-agent sync --creator douyin_12345 --days 3
```

## Doctor 检查项

| 检查项 | 通过条件 | 不通过时提示 |
|---|---|---|
| Python 版本 | >= 3.12 | 升级 Python |
| .venv | 存在 | `uv sync` |
| Playwright Chromium | 已安装 | `uv run playwright install chromium` |
| 浏览器 profile | 存在 | `creator-agent auth login` |
| storage 目录 | 可写 | 检查权限 |
| SQLite DB | 可写 | 检查路径权限 |
| FFmpeg | 可调用 | `uv sync`（Phase 2 需要） |
| 磁盘空间 | > 1GB | 清理 |

## 错误处理

**异常分层**：
```python
class CreatorAgentError(Exception): ...
class CollectorError(CreatorAgentError): ...
class DownloaderError(CreatorAgentError): ...
class StorageError(CreatorAgentError): ...
class RepositoryError(CreatorAgentError): ...
```

**重试**：
- Playwright 导航超时：重试 3 次，指数退避（2s/4s/8s）
- httpx 下载：重试 3 次
- Selector 找不到：不重试，抛 `CollectorError`

**日志**：loguru，控制台 + `logs/creator-agent.log`（10MB 轮转，7 天保留），结构化 `logger.bind(creator_id=..., video_id=..., stage=...)`。

**风险与缓解**：

| 风险 | 缓解 |
|---|---|
| 抖音 DOM 改版 | selectors 拆文件，parser 单测用 HTML fixture |
| 反爬限流 | 滚动间随机 sleep 1-3s，单 sync 限视频数 |
| 视频 URL 过期 | 采集后立即下载 |
| 登录态过期 | `auth login` 重新登录，doctor 检查 |
| 下载失败 | httpx 流式 + 断点续传 |

## 测试策略

```
tests/
├── unit/
│   ├── test_models.py
│   ├── test_time_parser.py
│   ├── test_collect_filter.py
│   ├── test_douyin_parser.py
│   └── test_scroll_strategy.py
├── integration/
│   ├── test_repository.py
│   ├── test_file_storage.py
│   └── test_pipeline_runner.py
└── e2e/
    └── test_douyin_real.py      # @pytest.mark.e2e,默认 skip
```

**关键单测**：断点续跑（验证状态机核心价值）- 模拟 video 卡在 METADATA_SAVED，sync 后推进到 VIDEO_DOWNLOADED，且不重跑 Collector。

## Phase 2 扩展点

| 模块 | 新增状态 | 读取 | 产出 |
|---|---|---|---|
| `asr/` (FunASR) | `ASR_DONE` | `status=VIDEO_DOWNLOADED` | `transcript.json` |
| `vision/` (Qwen2.5-VL) | `VISION_DONE` | `status=ASR_DONE` | `scene.json` |
| `analyzer/style.py` | `STYLE_DONE` | `status=ASR_DONE` | `style.json` |
| `analyzer/dna.py` | `DNA_DONE` | `status=STYLE_DONE` | `dna.json` |
| `knowledge/` | `KNOWLEDGE_DONE`（creator 级） | creator 下所有 `DNA_DONE` | creator 聚合 |
| `report/` | 无 | 最近 N 天 | `report.md` |
| `collector/bilibili/` | 无 | - | 复用所有状态 |

**扩展只需 3 步**：
1. 新增 `VideoStatus` 枚举值
2. 新增模块处理器（读某状态 -> 处理 -> 写文件 -> `advance_status`）
3. `PipelineRunner` 加阶段调用

## 配置

```yaml
# config/settings.yaml
storage_dir: "./storage"
db_path: "./storage/creator_agent.db"
browser:
  user_data_dir: "./storage/.browser_profile"
  headless: true
collector:
  scroll_delay_sec: [1, 3]
  out_of_window_page_threshold: 2
  max_videos_per_sync: 50
downloader:
  timeout_sec: 120
  retry: 3
log:
  level: "INFO"
  file: "./logs/creator-agent.log"
  rotation: "10 MB"
  retention: "7 days"
```

## Stable Interfaces（下游可并行开发的契约）

```python
class CollectFilter(BaseModel):
    start: datetime
    end: datetime
    max_videos: int | None = None

class CollectedVideo(BaseModel):
    platform: str
    platform_vid: str
    title: str
    description: str
    video_url: HttpUrl | None
    cover_url: HttpUrl | None
    published_at: datetime
    stats: VideoStats
    hashtags: list[str]

class Collector(ABC):
    @abstractmethod
    def collect(self, creator: Creator, filter: CollectFilter, browser: BrowserManager) -> list[CollectedVideo]: ...
```

这 3 个接口稳定后，Phase 2 的 Downloader 优化、ASR、Vision、Analyzer 可并行开发。
