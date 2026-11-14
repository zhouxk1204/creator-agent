# 哆啦A梦每周自动下载（定时任务）

每周六 19:00（北京时间）自动跑：**TVer 下载最新一集 → 抓取新播出集的简介 → 翻译成中文**。
TVer 每周六 17:00（日本时间 = 北京时间 16:00）更新，19:00 跑留 3 小时缓冲。

## 手动：最新话素材一条龙（最新话doraemon素材下载.bat）

完整的执行顺序、输入/输出文件、目录树、失败策略和优化观察项见 [`最新话doraemon素材下载.md`](./最新话doraemon素材下载.md)。

双击 `最新话doraemon素材下载.bat`，不输番号直接回车 = 最新已播出话全流程：
**抓简介/封面 → TVer 下载（命名为 `<番号>.mp4`）→ 分割 → 将 `<番号>_1.mp4`、`<番号>_2.mp4` 复制到 `C:\test\temps` → Real-ESRGAN 超分**。
所有素材汇总到桌面一个目录：`Desktop\doraemon_<番号>\`（metadata.json、封面、`<番号>.mp4`、分割片段 `<番号>_1.mp4` 等、`<番号>_split.json`、preview\）。日中标题/简介与发布标签统一存入 metadata v2，不再生成日中简介 Markdown。

**当前测试设置：**最新话 `.bat` / `.sh` 中 `DOWNLOAD_VIDEO=0`，默认只抓资料/封面并保存素材目录，跳过视频下载、分割、复制和视频超分；已有视频不动。改为 `DOWNLOAD_VIDEO=1` 才恢复上述完整视频流程。
超分结果输出为 `C:\test\<番号>_1_4K.mp4` 和 `C:\test\<番号>_2_4K.mp4`，日志在 `C:\test\run_log.txt`；已存在的输出会跳过。此入口不启动或关闭翻译模型。

- 输入了番号 = 只抓该话日文简介和封面，**不翻译、不下载、不分割、不超分**。
- TVer 下载按简介页的**放送日**匹配剧集（`--broadcast-date`），TVer 还没更新最新话时会在下载步停下并报错，不会把上一话错存成新番号。
- 标题/简介翻译与发布标签在 `06_correct_subtitles.bat` 内前置执行：按 `941_1_origin.srt` 匹配 metadata 的 `story_id`，更新同故事 `zh` 和 `publish`，后续字幕矫正和重翻再从 metadata 读取日中背景，共用一次模型启动。`02` 和下述每周定时任务的独立翻译也写入同一份 metadata。
- `06` 双击显示 `logs/.last_doraemon_materials` 中的上次素材目录，回车使用，或粘贴别的路径。有效的中文翻译默认复用，`--refresh-translation` 才强制刷新；`--force` 仅允许覆盖已有字幕。
- 后续抖音/B站发布同样按故事读取 metadata，不再使用文本发布包；标题、平台标签和独立封面路径规则见 [发布标准](../../docs/publish-package.md)。
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

