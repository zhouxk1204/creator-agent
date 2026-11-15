# publish_douyin.bat — 从 metadata.json 发布到抖音

## 使用方法

1. 先完成资料抓取、`06_correct_subtitles`，再用剪映把每集故事导出成
   `<素材目录>\<故事>_dy\<故事>_dy.mp4`（剪映里已加好字幕/画面的成品，一个文件对应一条抖音）。
   这一步不写 metadata，`publish_douyin.bat` 会自己去找。
2. 双击 `bat\publish_douyin.bat`，不用输入路径——直接用 `logs\.last_doraemon_materials`
   记录的素材目录。它分两步跑：

   - **[1/2] 生成封面**：只给已经有 `_dy` 导出的故事做横竖两张封面
     （`<故事>_douyin_portrait.jpg` 3:4、`<故事>_douyin_landscape.jpg` 4:3），
     并把两个路径写回 `metadata.json`；已有封面跳过（`--force` 重做）。
   - **[2/2] 循环发布**：把 `<故事>_dy\` 里所有 `*_dy.mp4` 逐个发布，一条视频一条抖音，
     每条用该故事的标题 / 标签 / 两张封面。

   要换目录就把路径当参数传，`--ask` 才临时询问。
3. 只发其中一集：`bat\publish_douyin.bat --story 942_1`。
4. 检查每条的视频、标题、标签、抖音声明、合集和封面摘要，输入 `Y` 确认后才开始发布（`--yes` 跳过）。
5. 浏览器窗口自动打开，可查看上传进度、封面、自主声明和合集设置过程。

建议第一次先校验，不实际上传：

```bat
bat\publish_douyin.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --dry-run
```

- 仅发布抖音，固定有头浏览器，不发布B站。
- 标题取 `publish.title`，格式 `哆啦a梦<集号> <中文标题>`（如 `哆啦a梦942 机器人服`）；抖音标签最多 5 个，简介就是这些 `#标签`。
- 两张封面由第一步自动生成并写回 `publish.douyin.cover_portrait_path`（3:4）和 `cover_landscape_path`（4:3），必须是两个独立文件；没生成就直接报“封面还没生成”，不会退回旧通用封面。
- 视频优先取 `publish.douyin.video_path`，没有就用 `<故事>_dy\<故事>_dy.mp4`，再退回 `publish.video_path`；不会扫描并猜测其它文件名。
- 同一个故事有多个 `_dy` 文件时，会用同一套标题/标签/封面各发一条（控制台会提示）。
- 默认路径来自仓库 `logs/.last_doraemon_materials`，直接使用、不再询问路径；`--ask` 才临时挑一次，手动粘贴只覆盖本次选择，不改变记录。
- 旧文本发布包不再支持，磁盘上已有文件不会自动删除。
- 自主声明：**虚构演绎，仅供娱乐**。
- 合集：**哆啦A梦2026合集**（需已存在于创作者账号）。
- 首次使用或缺少 social-auto-upload 时，先运行 `bat\setup_publish.bat`。
- 登录过期时，按 [`publish_dual.md`](./publish_dual.md) 的说明重新扫码。
