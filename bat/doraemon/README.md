# 哆啦A梦每周自动下载（定时任务）

每周六 19:00（北京时间）自动跑：**TVer 下载最新一集 → 抓取新播出集的简介 → 翻译成中文**。
TVer 每周六 17:00（日本时间 = 北京时间 16:00）更新，19:00 跑留 3 小时缓冲。

## 手动：最新话素材一条龙（最新话doraemon素材下载.bat）

双击 `最新话doraemon素材下载.bat`，不输番号直接回车 = 最新已播出话全流程：
**抓简介/封面 → 翻译 → TVer 下载（命名为 `<番号>.mp4`）→ 分割（弹出封面卡联系表，输入 y 确认切割）**。
所有素材汇总到桌面一个目录：`Desktop\doraemon_<番号>\`（story.md、封面、`<番号>.mp4`、分割片段 `<番号>_1.mp4` 等、`<番号>_split.json`、preview\）。

- 输入了番号 = 只抓该话简介 + 翻译，**不下载、不分割**（等同 02 的行为）。
- TVer 下载按简介页的**放送日**匹配剧集（`--broadcast-date`），TVer 还没更新最新话时会在下载步停下并报错，不会把上一话错存成新番号。
- 翻译失败不会中断流水线（简介翻译可事后跑 02 补）。

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
2. `scripts\fetch_doraemon.py --auto` — 从本地最大集数 +1 开始，抓取**已播出**新集的简介/封面（tv-asahi 会提前放出未播出集的页面，所以按页面上的放送日门控；漏跑的周会自动补抓，上限 8 集），抓到的集数写入 `storage\doraemon\.last_auto_fetched`
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
