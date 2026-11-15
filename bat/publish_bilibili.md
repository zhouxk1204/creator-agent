# publish_bilibili.bat — 从 metadata.json 批量发布到 B 站

## 使用方法

1. 先完成中文翻译和封面放大，并把每个故事的 B 站成品放到素材目录，命名为
   `<故事>_b.mp4`，例如 `942_1_b.mp4`、`942_2_b.mp4`。
2. 确认每个故事都有对应的 `<故事>_4k.jpg` 封面，例如 `942_1_4k.jpg`。
3. 双击 `bat\publish_bilibili.bat`。它直接使用 `logs\.last_doraemon_materials`
   记录的素材目录，分两步执行：
   - 把每个 `_b` 视频对应的 `<故事>_4k.jpg` 转成同尺寸、5 MiB 以内的真实 JPEG
     `<故事>_4k_bilibili.jpg`；原图不修改，避免 PNG 内容误用 `.jpg` 后缀导致封面接口 413。
   - 找出所有 `_b` 视频并逐个上传，不需要逐个选择故事。
4. 发布前会列出视频、标题、简介、标签和封面，输入 `Y` 才实际上传。
   只发一个故事时运行：

   ```bat
   bat\publish_bilibili.bat --story 942_1
   ```

建议先校验，不登录或上传（本地优化封面仍会生成）：

```bat
bat\publish_bilibili.bat "%USERPROFILE%\Desktop\doraemon_942" --dry-run
```

## 发布内容

- **视频**：素材目录根下的 `<故事>_b.mp4`；也支持 `.mov`、`.flv`、`.mkv`、`.avi`。
  只有文件名能对应 metadata 中 `story_id` 的视频才会发布。
- **标题**：`publish.title`，例如 `哆啦a梦942 机器人服`。
- **简介**：`zh.synopsis` 中文简介，完整保留换行，不使用日文 `synopsis`。
- **封面**：以上传的 `<故事>_4k_bilibili.jpg` 为准，它由对应的 `<故事>_4k.jpg` 自动转换，
  保持原始 4K 尺寸，不裁剪、不修改原图，也不会退回低分辨率的 `image_file`。
- **标签和分区**：继续使用 `publish.bilibili.tags` 和 `publish.bilibili.tid`。

路径参数、`--ask`、`--yes`、`--schedule`、`--tid`、`--bilibili-account` 等参数与
`publish_dual.bat` 相同。首次使用先运行 `bat\setup_publish.bat`；登录和上传故障处理见
[`publish_dual.md`](./publish_dual.md)。
