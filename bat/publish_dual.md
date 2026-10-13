# publish_dual.bat — 一键发布到 抖音 + B站

基于 [social-auto-upload](https://github.com/dreammis/social-auto-upload)（sau CLI，
vendored 在 `tools/social-auto-upload/`，独立 Python 3.12 venv，与主项目 .venv 互不影响）。

## 首次安装（只做一次）

双击运行：

```
bat\setup_publish.bat
```

它会自动：clone sau 仓库 → 建 Python 3.12 venv → 装 sau + playwright → 装 patchright
chromium 浏览器 → 生成 conf.py。可以重复运行，已完成的步骤会跳过。

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
    cover-douyin.jpg    建议，抖音封面 3:4 竖版
    cover-bilibili.jpg  建议，B站封面 16:10 横版
    cover.jpg           可选，通用封面回退
    meta.txt            可选，key=value 发布参数（tid/schedule/账号/跳过）
```

2. 双击 `bat\publish_dual.bat`（publish\ 下只有一个候选目录时自动选中），
   或显式指定：

```bat
bat\publish_dual.bat publish\2026-10-13-doraemon-935
```

3. 屏幕上会列出 目录/视频/标题/封面/平台 摘要，输入 `Y` 确认后开始发。
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

- **抖音触发短信验证**：sau 会等待验证码——把收到的验证码写进
  `tools\social-auto-upload\verify_code.txt`，或在终端提示时手动输入。
- **登录过期**：重新跑上面的 `sau ... login` 命令即可。可用
  `sau douyin check --account default` / `sau bilibili check --account default` 检查有效性。
- **B站首次运行慢**：sau 会自动从 GitHub 下载 biliup 上传器，需要能访问 GitHub。
- **想升级 sau**：`cd tools\social-auto-upload && git pull`，然后重跑
  `bat\setup_publish.bat`。注意 sau 上游正在重构期，升级前先在 `--dry-run` 下验证。
- 本工具与本项目的抖音采集登录（`storage\.browser_profile`）**互相独立**，互不影响。
