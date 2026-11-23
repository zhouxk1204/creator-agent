# 03_publish.bat — 哆啦A梦 B 站 / 抖音统一发布入口

双击 `bat\doraemon\03_publish.bat`，默认先发布 B 站，再发布抖音。直接使用
`logs\.last_doraemon_materials` 记录的素材目录；每个平台上传前都要输入 `Y` 确认。
原来的 `bat\publish_bilibili.bat`、`bat\publish_douyin.bat` 已合并到这里。

## 使用方法

首次使用先运行 `bat\setup_publish.bat`。完成中文翻译、字幕校正和视频导出后，准备：

- **B 站视频**：素材目录根下的 `<故事>_b.mp4`，例如 `942_1_b.mp4`。
- **B 站封面**：对应的 `<故事>_4k.jpg`，例如 `942_1_4k.jpg`。
- **抖音视频**：剪映导出的 `<素材目录>\<故事>_dy\<故事>_dy.mp4`，字幕和画面都已做好。
  每个 `*_dy.mp4` 对应一条抖音，不用手动写视频路径到 metadata。
- **抖音封面素材**：metadata 中已有中文标题，且对应故事的 `image_file` 图片存在。

```bat
rem 默认两个平台，先 B 站后抖音
bat\doraemon\03_publish.bat

rem 只发布一个平台
bat\doraemon\03_publish.bat --only bilibili
bat\doraemon\03_publish.bat --only douyin

rem 指定素材目录，只发布一个故事
bat\doraemon\03_publish.bat "%USERPROFILE%\Desktop\doraemon_942" --story 942_1

rem 先校验，不登录、不上传
bat\doraemon\03_publish.bat --dry-run
bat\doraemon\03_publish.bat --only douyin --story 942_1 --dry-run
```

默认双平台模式需要两边的成品视频；只准备好一个平台时使用 `--only`。
任一步失败或取消都会停止后续步骤，窗口保留错误信息；可修复后用 `--only` 重跑失败的平台，
避免重复发布已经成功的视频。

## B 站流程

1. **优化 4K 封面**：把所有待发布 `_b` 视频对应的 `<故事>_4k.jpg` 转成同尺寸、
   5 MiB 以内的真实 JPEG `<故事>_4k_bilibili.jpg`。不裁剪、不修改原图，避免 PNG 内容
   误用 `.jpg` 后缀导致封面接口 413。`--dry-run` 仍会生成这些本地优化封面。
2. **批量上传**：扫描素材目录根下的 `_b` 视频并逐个上传，不需要逐个选择故事。
   也支持 `.mov`、`.flv`、`.mkv`、`.avi`，只有文件名能对应 metadata 中 `story_id` 的视频才会发布。

发布内容：

- 标题：`publish.title`，例如 `哆啦a梦942 机器人服`。
- 简介：`zh.synopsis` 中文简介，完整保留换行，不使用日文 `synopsis`。
- 封面：上述 `<故事>_4k_bilibili.jpg`，不会退回低分辨率的 `image_file`。
- 标签和分区：`publish.bilibili.tags`、`publish.bilibili.tid`。

确认发布后，终端显示当前故事、文件名、大小，以及 `biliup` 实时进度、速度和预计剩余时间。
视频传到 100% 后还要等待稿件提交，看到“Draft submitted”和“成功”再关闭窗口。
失败时会显示命令退出码，最终结果同时带耗时。

## 抖音流程

1. **生成横竖封面**：只给已经有 `_dy` 导出的故事生成
   `<故事>_douyin_portrait.jpg`（3:4）和 `<故事>_douyin_landscape.jpg`（4:3），
   并写回 `publish.douyin.cover_portrait_path`、`cover_landscape_path`。已有封面跳过；
   如需强制重做，先单独运行 `bat\doraemon_legacy\07_make_covers.bat --force`。
2. **批量上传**：把 `<故事>_dy\` 里的所有 `*_dy.mp4` 逐个发布。
   固定打开浏览器窗口，可查看上传、封面、自主声明和合集设置过程。

发布内容：

- 标题：`publish.title`，格式 `哆啦a梦<集号> <中文标题>`。
- 简介：抖音 `#标签`，最多取前 5 个，超出时在控制台提示。
- 封面：必须是横竖两个独立文件；没有就报错，不退回旧通用封面。
- 同一故事有多个 `_dy` 文件时，用同一套标题、标签和封面分别发布，控制台会提示。
- 自主声明：**虚构演绎，仅供娱乐**。
- 合集：**哆啦A梦2026合集**（需已存在于创作者账号）。

`--dry-run` 不生成抖音封面，只打印封面计划并校验发布参数；首次还没有封面时，
发布校验会提示封面缺失，需要先用 `07_make_covers.bat` 生成。

## 参数与登录

- `--story 942_1`：只处理、发布该故事。
- `--only bilibili` / `--only douyin`：只运行对应平台的封面和上传流程。
- 路径参数：仅覆盖本次选择，不改 `logs/.last_doraemon_materials`。
- `--ask`（或 `--prompt-target`）：在各步骤临时询问素材目录，回车接受记录的目录；
  要确保所有步骤使用同一目录，建议直接传路径参数。
- `--yes`：明确跳过上传确认；默认仍需每个平台分别输入 `Y`。
- `--schedule`、`--tid`、`--douyin-account`、`--bilibili-account` 等发布参数继续支持。
  `--b` / `--dy` 由入口内部设置，选择平台请使用 `--only`。

发布前先检查 cookie，缺失或过期时自动打开登录窗口，扫码后继续；上传中途登录失效会重登并重试一次。
没有交互终端时会报错并给出登录命令。安装、登录和故障处理详见
[`../publish_dual.md`](../publish_dual.md)。通用单故事入口 `bat\publish_dual.bat` 仍然保留。
