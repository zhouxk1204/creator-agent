# publish_dual.bat — 从 metadata.json 发布到抖音 + B站

基于 [social-auto-upload](https://github.com/dreammis/social-auto-upload)（sau CLI，
vendored 在 `tools/social-auto-upload/`，独立 Python 3.12 venv，与主项目 .venv 互不影响）。

## 首次安装（只做一次）

双击运行：

```bat
bat\setup_publish.bat
```

它会自动：clone sau 仓库 → 应用抖音双封面和上传稳定性补丁 → 建 Python 3.12 venv → 装 sau + playwright →
安装修复抖音 0% 上传问题的 Patchright 1.64.4 → 装 patchright chromium 浏览器 → 生成 conf.py。
可以重复运行，已完成的步骤会跳过。

## 首次登录（只做一次，需要扫码）

在终端里交互执行（二维码登录，不能用脚本代替）：

```bat
tools\social-auto-upload\.venv\Scripts\sau.exe douyin login --account default --headed
tools\social-auto-upload\.venv\Scripts\sau.exe bilibili login --account default
```

- 抖音：弹出浏览器，扫码登录创作者中心；cookie 存在 `tools\social-auto-upload\cookies\douyin_default.json`。
- B站：终端里显示二维码（不完整的话打开生成的 `qrcode.png` 扫）；用哔哩哔哩 App 扫码。
- 想换账号名就把 `default` 改成别的名字（如 `main`），发布时传入 `--douyin-account main` / `--bilibili-account main`。

## 每次发布

1. 直接使用剧集素材目录的 `metadata.json`，不另做文本发布包。**唯一格式标准见 [`docs/publish-package.md`](../docs/publish-package.md)**。
2. 先完成 `06` 的中文简介翻译。`publish.video_path` 自动预填为 `<story_id>_final.mp4`，例如 `941_1_final.mp4`；剪映按该名字导出到素材目录即可。填写抖音两张封面路径，检查标题与平台标签；已有自定义视频路径保留。

```text
Desktop\doraemon_941\
    metadata.json                 唯一资料与发布配置
    941_1.jpg                     B站直接使用 image_file
    941_1_final.mp4                publish.video_path 默认预填，剪映导出使用此名称
    941_1_douyin_portrait.jpg      抖音竖封面，3:4
    941_1_douyin_landscape.jpg     抖音横封面，4:3
```

- 标题共用 `publish.title`，默认 `哆啦a梦941 XXXXX`。
- 抖音：`publish.douyin.tags` 最多 5 个，描述由这些 `#标签` 组成；两个封面路径必须指向不同文件。
- B站：`publish.bilibili.tags` 最多 11 个；描述直接读取 `zh.synopsis`，**保留换行**；封面直接读取 `image_file`，不重复配置。
- 所有相对素材路径以 metadata.json 所在目录为基准，允许绝对路径。目录可有多个视频，但不会自动猜选。
- 不再读取旧文本发布包或旧公共/通用封面目录，也不会自动删除原有文件。

3. 先校验：

```bat
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --dry-run
```

4. 双击 `bat\publish_dual.bat`：**不需要输入路径**，直接用 `logs/.last_doraemon_materials` 记录的素材目录（每次跑素材下载都会刷新）。多个故事时输入 `941_1` 等故事标识；要换目录就把路径当参数传，`--ask` 才临时询问（回车仍用默认）。也可显式指定：

```bat
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941\metadata.json" --story 941_1
```

5. 屏幕上列出 metadata/故事/视频/标题/标签/简介/封面摘要，输入 `Y` 后才开始上传。
   抖音自主声明固定选择 **「虚构演绎，仅供娱乐」**，设置失败会停止发布；合集固定选择
   **「哆啦A梦2026合集」**（需已存在于创作者账号，找不到时会警告并保持未归集）。
   先发抖音再发B站；一个失败不影响另一个。仅重试失败平台，避免重复发布已成功的平台：

```bat
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --only bilibili
```

## 抖音单平台快捷入口

只想发抖音时，双击 `bat\publish_douyin.bat`，选择素材目录和故事即可。
它固定使用有头浏览器，仍在发布前显示摘要并要求输入 `Y` 确认。详见 [`publish_douyin.md`](./publish_douyin.md)。

## 其他参数

```bat
:: 只校验和打印 sau 命令，不发布
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --dry-run
:: 明确跳过 Y 确认（自动化任务应同时指定路径和故事）
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --yes
:: 抖音上传时显示浏览器
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --headed
:: 覆盖定时发布配置和B站分区
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --schedule "2026-11-14 12:00" --tid 24
```

账号参数是 `--douyin-account` / `--bilibili-account`，默认均为 `default`。只发一个平台时仅校验该平台所需的标签和封面。

## 常见问题

- **一直显示“文件解析中 0%”，约 60 秒后报“网络异常”**：这是 Patchright 1.58.2
  改写页面 CSP、阻止抖音上传组件启动 `blob:` Worker 导致的，并不是视频真的已经上传但网速慢。
  运行一次 `bat\setup_publish.bat`，它会安装上游验证过的 Patchright **1.64.4** 和匹配浏览器。
  上传没有越过这一步时，视频预览和封面设置也不会生成；先修复上传，再检查封面。
- **看不到抖音上传进度条**：双平台入口默认无头，不显示窗口。加 `--headed`，或使用 `publish_douyin.bat`：
  ```bat
  bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --only douyin --headed
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
