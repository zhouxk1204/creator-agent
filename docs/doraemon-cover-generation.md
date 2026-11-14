# 抖音竖版封面：自动生成方案

> 目标：把「剪映里手工做 3:4 封面」变成一条命令，并自动写回
> `metadata.json` 的 `publish.douyin.cover_portrait_path`。

## 1. 现状与约束

`metadata.json` v2 要求抖音两张封面**必填**：`cover_portrait_path`（3:4）与
`cover_landscape_path`（4:3），且两者不能指向同一文件。现在两张都是人工在剪映里做的。

自动生成受三条硬约束：

| 约束 | 影响 |
| --- | --- |
| 封面标题用**中文**（`zh.title`），而中文是 02（抓取后翻译）或 06（字幕矫正前置翻译）才写进 metadata 的 | 生成必须排在中文标题之后 |
| 渲染依赖**剪映 GUI**（花字是剪映私有效果，Pillow 复刻不了），Windows 单机、每次要起剪映约 30–40 秒、会抢占鼠标键盘 | 不能放进无人值守的定时任务 |
| 剪映 11.7 加密草稿且移除了 `videoeditor.dll` 的加解密导出，只能**新建明文草稿**让它渲染 | 每次生成都会 `taskkill` 剪映 → 不能在"正开着剪映剪片"的时候跑 |

## 2. 时机选择：三个候选

| 时机 | 中文标题 | 机器状态 | 问题 |
| --- | --- | --- | --- |
| A. 02 抓取时 | 02 末尾会翻译 → 有；但「最新话素材下载」入口是**延迟翻译**，这时没有 | 刚下载完，空闲 | 同一集常被重跑（`02` 幂等重抓），每次重跑都重启剪映；定时任务 `weekly_sync` 里跑剪映会弹窗/抢焦点 |
| B. 06 字幕矫正后 | 有，而且是**最终版**（06 会刷新翻译） | 人工双击、机器空闲 | 需要多记一步；但正好在"人工确认成片/字幕"这个节点，顺手就能检查封面 |
| C. 发布抖音时 | 有 | Playwright 上传正在进行 | 发布要么是无人值守/定时（`--schedule`），要么人工盯着；此时重启剪映、抢键鼠会打断上传，失败还会卡住发布 |

**结论：主入口放 B（`07_make_covers`，排在 06 之后、发布之前）；发布侧只做校验，不自动生成。**

理由：标题已经定稿、机器空闲、人工在场；发布流程保持纯"读 metadata → 上传"，
不引入会失败的外部进程。真忘了跑 07，发布时报的是明确的"封面缺失"而不是跑一半崩掉。

如果以后想更省事，可以在 `publish_dual.py` 加 `--auto-cover` 显式触发（默认关闭），
但**不建议**把它设成默认行为。

## 3. 实现结构

```text
src/creator_agent/doraemon/covers.py          纯逻辑：选故事、取标题、路径规则、写回 metadata
src/creator_agent/doraemon/jianying_cover.py  剪映渲染器（GUI，懒加载 jianying_tool）
scripts/make_douyin_covers.py                 CLI：读 metadata → 生成 → 写回
bat/doraemon/07_make_covers.bat               双击入口
assets/doraemon_cover_template/               封面模板（明文时间线 + 草稿骨架 + 两个图层）
tests/unit/test_doraemon_covers.py            路径/写回/幂等规则（不碰剪映）
```

渲染原理：模板草稿是 1440×1920、5 帧的静帧工程（图片 + 黑底白圆角边框 + 三行字）。
`jianying_cover` 复制骨架 → 写一份**明文** `draft_content.json`（换图、标题那行写故事标题、
集数那行写集号、`Doraemon` 固定不动）→ 启动剪映打开草稿 → 走剪映自己的导出对话框（原始分辨率）
→ ffmpeg 抽首帧 → 存成 JPG。

## 4. 用法

```bat
:: 先看看要生成什么（不起剪映、不写文件）
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942" --dry-run

:: 生成全部故事
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942"

:: 只做一条 / 已有封面也重做
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942" --story 942_1
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942" --force
```

不传目录时用 `logs/.last_doraemon_materials` 里上次的素材目录。

输出：`<素材目录>/<story_id>_douyin_portrait.jpg`（1440×1920），并把
`publish.douyin.cover_portrait_path` 写成相对文件名（如 `942_1_douyin_portrait.jpg`）。

## 5. 写回规则（重要）

- **只填空缺或文件已丢失的路径**：已有封面文件且不是本次生成的文件时，直接拒绝，加 `--force` 才覆盖。
  人工做的封面不会被一次误跑冲掉。
- 标题取 `zh.title`，**翻译缺失/过期直接报错**，不回退日文标题（避免发出错封面）。
- 只写 `cover_portrait_path`；`cover_landscape_path` 不动。
- 写文件走 `metadata.update_metadata`（文件锁 + 原子替换），多故事连续生成不会互相覆盖。

## 6. 横版（4:3）封面怎么办

竖版模板现成，横版没有对应版式——4:3 需要重新排版（图片区、黑底条、三行字的位置都不一样），
硬把 1440×1920 的时间线改成 1440×1080 会走形。

两条路，二选一：

1. **在剪映里复制一版 4:3 模板**（画布 1440×1080，图片铺满、标题和集数放左下/右下），
   导出一次明文时间线给我 → 我把它放进 `assets/doraemon_cover_template_landscape/`，
   渲染器按同样的替换规则出图，`--landscape` 一起写回。**推荐**：版式由你把关，效果和竖版一致。
2. 先用竖版渲染、再用 Pillow 把竖版重排成 4:3 —— 花字会被重新缩放/裁切，效果不可控，不建议。

在横版模板到位之前，`cover_landscape_path` 继续人工填；发布前 `publish_dual.py` 会照常校验。

## 7. 失败与验收

- 没有剪映 / 非 Windows / 没装 `covers` extra → 明确报错，提示 `uv sync --extra covers`，不影响其它故事。
- 打开错草稿（剪映启动时草稿列表还没扫完）→ 自动重试 3 次（每次重启剪映、多等 5 秒）。
- 单条失败不影响其它条，最后汇总 `生成 N，跳过 N，失败 N`，有失败时退出码 1。
- 生成完统一删草稿并清理 `root_meta_info.json` 里的悬空索引项，不污染剪映草稿列表。

验收：

1. `--dry-run` 能列出每条故事的目标文件名与标题。
2. 真跑后 `<story_id>_douyin_portrait.jpg` 存在，尺寸 1440×1920，画面与剪映里手工导出一致。
3. `metadata.json` 的 `cover_portrait_path` 被写成相对文件名，`publish_dual.py --dry-run` 能通过校验。
4. 再跑一次 → 全部 `SKIP`（幂等）；`--force` 才会重做。
