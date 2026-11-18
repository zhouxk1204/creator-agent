# 哆啦A梦每周自动下载（定时任务）

每周六 19:00（北京时间）自动跑：**TVer 下载最新一集 → 抓取新播出集的简介 → 翻译成中文**。
TVer 每周六 17:00（日本时间 = 北京时间 16:00）更新，19:00 跑留 3 小时缓冲。

## 手动：最新话素材一条龙（01_download_latest_materials.bat）

完整的执行顺序、输入/输出文件、目录树、失败策略和优化观察项见 [`01_download_latest_materials.md`](./01_download_latest_materials.md)。

双击 `01_download_latest_materials.bat`，不输番号直接回车：优先恢复上次未完成剧集，否则处理最新已播出话。恢复时不查询最新索引，使用保存的集号和播出日期；输入或传入 `--latest` 可忽略待恢复记录。
**解析/恢复集号和日期 → TVer 下载（命名为 `<番号>.mp4`）→ 分割 → 在素材目录内对 `<番号>_1.mp4`、`<番号>_2.mp4` 依次执行 Real-ESRGAN 超分**；标题、简介、封面抓取和封面放大作为另一条分支并行执行，不阻塞视频主线。
所有素材汇总到桌面一个目录：`Desktop\doraemon_<番号>\`（metadata.json、封面、`<番号>.mp4`、分割片段 `<番号>_1.mp4` 等、超分视频、`<番号>_split.json`、preview\）。封面放大成功后，metadata 的 `image_file` 改为放大图文件名，再删除原图；放大失败保留原图。日中标题/简介与发布标签统一存入 metadata v2，不再生成日中简介 Markdown。

**当前默认设置：**Windows `.bat` 中 `DOWNLOAD_VIDEO=1`，直接回车运行完整视频流程；macOS/Linux `.sh` 中仍为 `DOWNLOAD_VIDEO=0`，默认只抓资料/封面并保存素材目录，跳过视频下载和分割，已有视频不动。将实际使用的启动器设为 `DOWNLOAD_VIDEO=0` 可切换为仅资料模式，设为 `1` 则启用视频流程。
超分结果输出为素材目录内的 `<番号>_1_4K.mp4` 和 `<番号>_2_4K.mp4`。恢复按视频进行：已完成的非空输出跳过，未完成视频清理残留后从抽帧重做，不恢复中间帧。每段完成、失败或 Ctrl+C 后清理临时帧和 `.part.mp4`，保留源视频；强制关窗的残留在重跑时清理。`run_upscale.bat` 支持明确的视频路径、`--output-dir` 和 `--force`。
资料输出和视频进度实时显示，带时间戳追加到 `materials_log.txt`、`pipeline_log.txt`、`run_log.txt`。资料失败只提示警告，不改变视频主线退出码，但仍保留待恢复状态。独立的 `logs/.last_doraemon_pipeline` 与目录内 `pipeline_state.json` 记录恢复信息，不被仅资料模式覆盖；两条分支成功结束后才标记整集完成。正常退出前等待资料分支，中断时停止子进程。此入口不启动或关闭翻译模型。

- 输入了番号 = 只抓该话日文简介和封面，**不翻译、不下载、不分割、不超分**。
- TVer 下载按故事索引的**播出日**匹配剧集（`--broadcast-date`），TVer 还没更新最新话时会在下载步停下并报错，不会把上一话错存成新番号。
- 标题/简介翻译与发布标签在 `06_correct_subtitles.bat` 内前置执行：按 `941_1_origin.srt` 匹配 metadata 的 `story_id`，更新同故事 `zh` 和 `publish`，后续字幕矫正和重翻再从 metadata 读取日中背景，共用一次模型启动。`02` 和下述每周定时任务的独立翻译也写入同一份 metadata。
- `04` 双击读取 `logs/.last_doraemon_materials`，把其中记录的素材目录同时作为输入和输出根目录；每个视频的产物位于 `<素材目录>/<视频名>/`。要临时换目录可传 `--input-dir` / `--out-dir`。
- `06` 双击**不需要输入路径**：直接用 `logs/.last_doraemon_materials`（上次素材下载刚刷新过）开始处理；要换目录就把路径当参数传，`--ask` 才临时询问。有效的中文翻译默认复用，`--refresh-translation` 才强制刷新；`--force` 仅允许覆盖已有字幕。
- 后续抖音/B站发布同样按故事读取 metadata，不再使用文本发布包；标题、平台标签和独立封面路径规则见 [发布标准](../../docs/publish-package.md)。
- **统一发布入口 `03_publish.bat`**：默认先发布素材目录根下的 `<故事>_b.mp4` 到 B 站，再发布 `<故事>_dy/` 里的剪映导出到抖音；自动处理各平台封面，上传前分别确认。只发一个平台用 `--only bilibili` / `--only douyin`，也支持 `--story`、`--dry-run`；详见 [发布使用说明](./03_publish.md)。
- 抖音**横竖两张封面**都可自动生成：`07_make_covers.bat`（排在 `06` 之后、发布之前）。它用剪映封面模板渲染 `<story_id>_douyin_portrait.jpg`（1440×1920）和 `<story_id>_douyin_landscape.jpg`（1440×1080），并写回 `publish.douyin.cover_portrait_path` / `cover_landscape_path`；已有封面不会覆盖（`--force` 重做，`--covers portrait|landscape` 只做一张）。原理与时机见 [封面自动生成](../../docs/doraemon-cover-generation.md)。
- 超分依赖本机已有的 `C:\to4k\Real-ESRGAN`（含模型与 ffmpeg）和 `C:\Users\34696\miniconda3\envs\upscale` 环境；项目内的 `run_upscale.bat` / `scripts\upscale_tool.py` 只是流程入口，不包含第三方引擎本体。

## 一次性安装（家里 Windows 机）

```
git pull
双击 bat\doraemon\install_weekly_task.bat
```

它会在任务计划程序里注册任务 `DoraemonWeeklySync`（每周六 19:00，/F 覆盖旧的，重跑即更新）。

**前提**：周六晚上电脑保持开机、且用户已登录（任务计划默认只在登录时运行）。

## 日常检查

```bat
:: 立即手动触发一次（不用等周六）
schtasks /Run /TN DoraemonWeeklySync

:: 查看任务状态和上次运行结果
schtasks /Query /TN DoraemonWeeklySync /V /FO LIST
```

日志追加写在 **`logs\weekly_doraemon.log`**，每周一段，出问题先看它。

## 它每周做了什么（weekly_sync.bat）

1. `scripts\download_tver.py` — 从 TVer 下载最新一集到桌面生肉目录（已下载的文件 yt-dlp 自动跳过，重复跑无害）
2. `scripts\fetch_doraemon.py --auto` — 从桌面已抓取的最大集数 +1 开始，抓取**已播出**新集的简介/封面（tv-asahi 会提前放出未播出集的页面，所以按页面上的放送日门控；漏跑的周会自动补抓，上限 8 集），抓到的集数写入 `logs\.last_auto_fetched`
3. 有新集时：自动起 llama.cpp 翻译服务器（没起的话），逐集跑 `scripts\translate_doraemon.py`，自己起的服务器跑完自动关

任何一步失败都会停在失败处并写日志，不影响下周再跑（下载和 --auto 都是幂等的）。

## 卸载

```bat
schtasks /Delete /TN DoraemonWeeklySync /F
```

## 常见问题

- **下载失败、提示 proxy**：从国内访问 TVer 需要日本代理。定时任务和你手动双击 01 用的是同一用户环境，手动能跑通定时就能跑通；不行的话先确认代理（如 Clash）周六晚上也在运行。
- **翻译步骤失败**：看日志里 llama.cpp 是否起来了（`[server]` 行）；手动跑过一次 `02_fetch_doraemon.bat` 能通，定时就能通。
- **想改时间**：改 `install_weekly_task.bat` 里的 `/ST 19:00` 再双击一次即可。

## 后续

等 ASR 质量到位后，把 `03_split_episode` / `04_ja_asr` / `05_learn_corrections` 追加进 `weekly_sync.bat`（文件里有注释标记位置），整条 pipe 就能每周全自动跑完。

