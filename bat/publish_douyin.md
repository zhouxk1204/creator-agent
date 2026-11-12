# publish_douyin.bat — 从 metadata.json 发布到抖音

## 使用方法

1. 先完成资料抓取和 `06_correct_subtitles`，在素材目录的 `metadata.json` 中填写所选故事的最终 `publish.video_path`、抖音竖/横封面路径，检查标题与标签。完整格式见 [`docs/publish-package.md`](../docs/publish-package.md)。
2. 双击 `bat\publish_douyin.bat`。它显示上次素材目录，直接回车使用；也可以粘贴另一个目录或 metadata.json 路径，例如：

   ```text
   C:\Users\你的用户名\Desktop\doraemon_941
   ```

   路径可以带引号、空格；也可以将目录或 metadata.json 拖到 BAT 上。
3. metadata 有多个故事时，输入要发布的故事标识，如 `941_1`。
4. 检查视频、标题、标签、抖音声明、合集和两张封面摘要，输入 `Y` 确认后才开始发布。
5. 浏览器窗口自动打开，可查看上传进度、封面、自主声明和合集设置过程。

建议第一次先校验，不实际上传：

```bat
bat\publish_douyin.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --dry-run
```

- 仅发布抖音，固定有头浏览器，不发布B站。
- 标题默认格式 `哆啦a梦941 XXXXX`；抖音标签最多 5 个，描述直接使用这些 `#标签`。
- 两张封面分别填写 `publish.douyin.cover_portrait_path`（3:4）和 `cover_landscape_path`（4:3）。必须是两个独立文件，没有旧公共目录/通用封面回退。
- 素材相对路径以 metadata.json 的目录为基准，绝对路径也支持；不会扫描并猜测视频。
- 默认路径来自仓库 `logs/.last_doraemon_materials`。手动粘贴只覆盖本次选择，不改变记录。
- 旧文本发布包不再支持，磁盘上已有文件不会自动删除。
- 自主声明：**虚构演绎，仅供娱乐**。
- 合集：**哆啦A梦2026合集**（需已存在于创作者账号）。
- 首次使用或缺少 social-auto-upload 时，先运行 `bat\setup_publish.bat`。
- 登录过期时，按 [`publish_dual.md`](./publish_dual.md) 的说明重新扫码。
