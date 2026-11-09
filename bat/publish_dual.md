# publish_dual.bat — 一键发布到 抖音 + B站

基于 [social-auto-upload](https://github.com/dreammis/social-auto-upload)（sau CLI，
vendored 在 `tools/social-auto-upload/`，独立 Python 3.12 venv，与主项目 .venv 互不影响）。

## 首次安装（只做一次）

双击运行：

```
bat\setup_publish.bat
```

它会自动：clone sau 仓库 → 应用抖音双封面和上传稳定性补丁 → 建 Python 3.12 venv → 装 sau + playwright →
安装修复抖音 0% 上传问题的 Patchright 1.64.4 → 装 patchright chromium 浏览器 → 生成 conf.py。
可以重复运行，已完成的步骤会跳过。

## 首次登录（只做一次，需要扫码）

在终端里交互执行（二维码登录，不能用脚本代替）：

```bat
tools\social-auto-upload\.venv\Scripts\sau.exe douyin   login --account default --headed
tools\social-auto-upload\.venv\Scripts\sau.exe bilibili login --account default
```

- 抖音：弹出浏览器，扫码登录创作者中心；cookie 存在
  `tools\social-auto-upload\cookies\douyin_default.json`。
- B站：终端里显示二维码（不完整的话打开生成的 `qrcode.png` 扫）；用哔哩哔哩 App 扫码。
- 想换账号名就把 `default` 改成别的名字（如 `main`），发布时在 meta.txt 里对应改
  `douyin_account=` / `bilibili_account=`。

## 每次发布

1. 建好发布目录，把素材放进去。**格式标准见 `docs/publish-package.md`**（唯一权威定义，
   含给 AI 的生成提示词）；模板可直接复制：`docs/publish-template/`。

```
publish\2026-10-13-doraemon-935\
    video.mp4           必须，且目录里只能有一个视频文件
    title.txt           必须，第一行 = 标题（≤55字）
    desc.txt            建议，整个文件 = 简介（多行可以）
    tags.txt            建议，标签，逗号/空格/换行分隔都行（≤12个）
    cover-douyin-portrait.jpg  建议，抖音竖封面 3:4
    cover-douyin-landscape.jpg 建议，抖音横封面 4:3
    cover-douyin.jpg           兼容旧格式，抖音竖封面 3:4
    cover-bilibili.jpg         建议，B站封面 16:10 横版
    cover.jpg                  可选，通用封面回退
    meta.txt            可选，key=value 发布参数（tid/schedule/账号/跳过）
```

抖音支持两张可复用公共封面，放到：

```text
C:\Users\34696\Desktop\publish-template\
    cover-douyin-portrait.jpg     竖封面，建议 1080×1440（3:4）
    cover-douyin-landscape.jpg    横封面，建议 1440×1080（4:3）
```

每次上传会自动使用这两张图；发布目录中的同名文件优先。也支持 `.jpeg` / `.png`。
不需要每次复制公共封面，旧的 `cover-douyin.jpg` 仍可作为竖封面使用。

1. 双击 `bat\publish_dual.bat`（publish\ 下只有一个候选目录时自动选中），
   或显式指定：

```bat
bat\publish_dual.bat publish\2026-10-13-doraemon-935
```

1. 屏幕上会列出 目录/视频/标题/封面/平台 摘要，输入 `Y` 确认后开始发。
   先发抖音，再发B站；一个失败不影响另一个，失败的可以单独重跑：

```bat
bat\publish_dual.bat publish\xxx --only bilibili
```

## meta.txt 支持的键

见 `docs/publish-package.md` 第 2 节（含 B站分区 tid 速查）。

## 其他参数

```bat
bat\publish_dual.bat publish\xxx --dry-run      只打印将要执行的 sau 命令，不发布
bat\publish_dual.bat publish\xxx --yes          跳过 Y 确认（配合计划任务用）
bat\publish_dual.bat publish\xxx --headed       抖音上传时显示浏览器窗口（排错用）
bat\publish_dual.bat publish\xxx --schedule "2026-10-14 12:00"
```

## 常见问题

- **一直显示“文件解析中 0%”，约 60 秒后报“网络异常”**：这是 Patchright 1.58.2
  改写页面 CSP、阻止抖音上传组件启动 `blob:` Worker 导致的，并不是视频真的已经上传但网速慢。
  运行一次 `bat\setup_publish.bat`，它会安装上游验证过的 Patchright **1.64.4** 和匹配浏览器。
  上传没有越过这一步时，视频预览和封面设置也不会生成；先修复上传，再检查封面。
- **看不到抖音上传进度条**：默认使用无头浏览器，不会显示窗口。重跑时加 `--headed`：
  ```bat
  bat\publish_dual.bat publish\xxx --only douyin --headed
  ```
  终端也会每 10 秒打印页面实际上传状态（包含页面提供的百分比）。
- **抖音反复显示“上传失败”**：当前版本最多尝试 3 次、总等待上限 30 分钟，不会无限重传。
  失败时终端会打印页面原因及上传网络错误，并在 `tools\social-auto-upload\logs\` 保存
  `douyin_upload_failure_*.png` 截图。请先看具体错误，不要盲目重复发布；必要时用上面的
  `--headed` 命令观察页面。视频建议转为 H.264 + AAC、`yuv420p` 的 MP4 后重试。
- **抖音触发短信验证**：sau 会等待验证码——把收到的验证码写进
  `tools\social-auto-upload\verify_code.txt`，或在终端提示时手动输入。
- **登录过期**：重新跑上面的 `sau ... login` 命令即可。可用
  `sau douyin check --account default` / `sau bilibili check --account default` 检查有效性。
- **B站首次运行慢**：sau 会自动从 GitHub 下载 biliup 上传器，需要能访问 GitHub。
- **想升级 sau**：`cd tools\social-auto-upload && git pull`，然后重跑
  `bat\setup_publish.bat`。注意 sau 上游正在重构期，升级前先在 `--dry-run` 下验证。
- 本工具与本项目的抖音采集登录（`storage\.browser_profile`）**互相独立**，互不影响。
