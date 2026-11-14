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

渲染原理：模板草稿是 5 帧的静帧工程（图片 + 白圆角边框 + 三行字）。
`jianying_cover` 复制骨架 → 写一份**明文** `draft_content.json`（换图、标题那行写故事标题、
集数那行写集号、`Doraemon` 固定不动）→ 启动剪映打开草稿 → 走剪映自己的导出对话框（原始分辨率）
→ ffmpeg 抽首帧 → 按需叠白框 → 存成 JPG。

两套模板（都在 `assets/`）：

| 模板 | 画布 | 说明 |
| --- | --- | --- |
| `doraemon_cover_template` | 1440×1920（3:4） | 竖版，直接用你原来的模板：图片 + 黑底白圆角边框层 + 三行字 |
| `doraemon_cover_template_landscape` | 1440×1080（4:3） | 横版，只保留三行字（花字）；图片裁切/渐隐和白框由渲染器合成 |

横版为什么这样拆：3:4 的边框/卡片图层（960×1280、804×1072）放到 4:3 画布上会被剪映按比例
重新适配，白框会缩到画面中间；而白框、底部渐隐都是静态美术，交给 Pillow 反而能 1:1 复刻
（`prepare_photo` 里遮罩渐隐，`render_cover` 里叠 `layers/border_ring.png`）。
花字是剪映私有效果，所以仍然由剪映渲染。

## 4. 用法

```bat
:: 先看看要生成什么（不起剪映、不写文件）
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942" --dry-run

:: 生成全部故事（横竖各一张）
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942"

:: 只做一条 / 只做横版 / 已有封面也重做
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942" --story 942_1
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942" --covers landscape
bat\doraemon\07_make_covers.bat "%USERPROFILE%\Desktop\doraemon_942" --force
```

不传目录时用 `logs/.last_doraemon_materials` 里上次的素材目录。

输出：

- `<素材目录>/<story_id>_douyin_portrait.jpg`（1440×1920）→ `publish.douyin.cover_portrait_path`
- `<素材目录>/<story_id>_douyin_landscape.jpg`（1440×1080）→ `publish.douyin.cover_landscape_path`

两者都写成相对文件名（如 `942_1_douyin_portrait.jpg`），路径以 metadata.json 所在目录为基准。
每条故事横竖各渲染一次，所以单条耗时约 2 倍（一次剪映启动只出一张）。

## 5. 写回规则（重要）

- **只填空缺或文件已丢失的路径**：已有封面文件且不是本次生成的文件时，直接拒绝，加 `--force` 才覆盖。
  人工做的封面不会被一次误跑冲掉。
- 标题取 `zh.title`，**翻译缺失/过期直接报错**，不回退日文标题（避免发出错封面）。
- 横竖分开判断：只覆盖各自字段/文件，竖版已有不影响横版生成。
- 写文件走 `metadata.update_metadata`（文件锁 + 原子替换），多故事连续生成不会互相覆盖。

## 6. 横版（4:3）封面怎么办

已实现，见第 3 节的横版模板。数值来自你的 `横屏封面模版` 草稿渲染出的 1440×1080 参考图：

| 项 | 值 |
| --- | --- |
| 白框 | 左 25、右 26、上 17、下 23，线宽 7，圆角 34（叠在成品上） |
| 图片 | 4:3 居中裁切铺满，底部 660→900 px 渐隐到黑 |
| 标题 / Doraemon / 集数 | 纵向 -0.395 / -0.554 / -0.719（与竖版同样的三行位置） |

想微调版式，改 `src/creator_agent/doraemon/jianying_cover.py` 顶部的
`FADE_START / FADE_END` 和 `assets/doraemon_cover_template_landscape/template.json` 里三行文字的 `transform.y`。

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

