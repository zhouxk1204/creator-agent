# publish_douyin.bat — 粘贴路径发布到抖音

## 使用方法

1. 双击 `bat\publish_douyin.bat`。
2. 看到 `Publish folder path:` 后，粘贴**发布文件夹**路径并按回车，例如：

   ```text
   C:\Users\34696\Desktop\publish-template-h264
   ```

   路径可以带引号，也可以带空格。也可以直接把文件夹拖到该 BAT 图标上。

3. 检查视频、标题、抖音声明、合集和封面摘要，输入 `Y` 确认后开始发布。
4. 浏览器窗口会自动打开，可查看上传进度、封面、自主声明和合集设置过程。

发布目录必须包含一个视频和 `title.txt`，完整格式见 `docs/publish-package.md`。
脚本只发布到抖音，固定使用有头浏览器；不会发布到B站。

- 自主声明：**虚构演绎，仅供娱乐**。
- 合集：**哆啦A梦2026合集**（需已存在于创作者账号）。
- 首次使用或提示缺少 social-auto-upload 时，先运行 `bat\setup_publish.bat`。
- 登录过期时，按 `bat/publish_dual.md` 的登录说明重新扫码。
