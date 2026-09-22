# Web UI — 浏览已采集视频

## 目标
本地网页浏览已同步的视频：封面缩略图、点击播放（可拖动进度条）、视频元信息（标题/描述/日期/标签）、点赞等数据、以及 ASR 完整解说文案。

## 方案：stdlib `http.server`，零新依赖
项目刻意保持最小依赖（typer/pydantic/playwright/httpx/pyyaml）。网页浏览器是本地单用户工具，因此用 Python 内置 `http.server` + 自定义 handler，**不引入 FastAPI/Flask/uvicorn**。

唯一真实技术点是 **HTTP Range 支持**：`<video>` 标签在 50MB mp4 上拖动进度条需要服务器支持 Range 请求。stdlib的 `SimpleHTTPRequestHandler` 只能整文件发送、无法 seek。我们实现一个小的 Range 文件流（解析 `Range: bytes=...`，返回 `206 Partial Content` + `Content-Range`）。

## 新增文件
- `src/creator_agent/web/__init__.py`
- `src/creator_agent/web/server.py` — handler、路由、Range 流式响应、server 工厂
- `src/creator_agent/web/templates.py` — 列表页 + 详情页 HTML/CSS（Python 字符串函数，少量原生 JS 做筛选/搜索）
- `tests/unit/test_web_server.py`

## 修改文件
- `src/creator_agent/cli/main.py` — 新增 `web` 命令（`--host 127.0.0.1 --port 8000`）

## 路由
| 路由 | 用途 |
|---|---|
| `GET /` | 列表页：视频卡片网格（封面、标题、日期、创作者、❤️ 点赞、状态徽标、标签）。客户端按创作者筛选 + 文本搜索。按 published_at 倒序 |
| `GET /video/{video_id}` | 详情页：左侧 `<video controls poster=cover>` 播放器，右侧元信息面板（标题/描述/发布&采集时间/数据/标签/原链接），下方完整文案（带时间戳分段 + 复制按钮）。未转写显示"暂无文案" |
| `GET /cover/{video_id}` | 返回 cover.jpg（image/jpeg） |
| `GET /stream/{video_id}` | mp4 流式响应，**支持 Range/206** 以便拖动进度条 |
| `GET /avatar/{creator_id}` | 返回创作者 avatar.jpg |

## 数据流
- 复用 settings/repo/storage（轻量初始化，不像 `_init_components` 那样拉起 browser/downloader）
- 列表：`repo.list_creators()` → 每个 `repo.list_videos(creator_id)` → 卡片。`has_transcript` = `status==ASR_DONE` 或 `storage.load_transcript()` 存在
- 详情：`repo.get_video(id)` + `repo.get_creator(creator_id)` + `storage.load_transcript()` + 经 `FileStorage` 取 cover/video 路径

## 路径安全
媒体端点只接收 `video_id`/`creator_id`，路径经 repo + FileStorage 解析。不接受原始文件系统路径。`..` 与未知 id → 404。杜绝目录穿越。

## CLI
```
uv run creator-agent web                 # 127.0.0.1:8000
uv run creator-agent web --port 8080     # 自定义端口
```
启动后打印访问地址，`Ctrl+C` 退出。

## 测试 `tests/unit/test_web_server.py`
- 路由解析（list/detail/cover/stream → 正确 handler）
- Range 解析：`bytes=0-1023`、`bytes=0-`、开放区间
- 206 响应 + `Content-Range` 头正确
- 路径穿越：id 含 `..` → 404
- 未知 video_id → 404
- 文案存在 → 渲染；不存在 → "暂无文案"
- 用 stdlib `http.client` 对绑定临时端口的 server 发请求（或直接测 handler）

## 不在本次范围
- AI 分析/统计仪表盘（后续阶段）
- md 导出（网页已展示文案；如仍需 md 可后加）
- 编辑/标注功能
