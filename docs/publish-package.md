# 发布包标准（publish package spec）

这是「一键发布到 抖音 + B站」的**唯一格式标准**。人工或 AI 准备素材时都按这个标准
产出，`scripts/publish_dual.py`（`bat\publish_dual.bat`）严格按此读取。

## 1. 目录结构

```
publish/<slug>/                  # slug 建议: 日期-内容，如 2026-10-14-doraemon-935
├── video.mp4                    # 【必填】视频，目录里只能有一个
├── title.txt                    # 【必填】标题
├── desc.txt                     # 【建议】简介/描述
├── tags.txt                     # 【建议】标签
├── cover-douyin.jpg             # 【建议】抖音封面，3:4 竖版
├── cover-bilibili.jpg           # 【建议】B站封面，16:10 横版
├── cover.jpg                    # 【可选】通用封面（上面两个缺失时的回退）
└── meta.txt                     # 【可选】发布参数
```

可直接复制模板：`docs/publish-template/` → `publish/<slug>/`，然后改内容。

## 2. 逐字段说明

### video.mp4 — 视频（必填）

- 目录里**有且仅有一个**视频文件（`.mp4 .mov .flv .mkv .avi`），文件名任意。
- 建议 H.264 + AAC 的 mp4；竖版 1080×1920 对抖音最友好，B站横竖都收。

### title.txt — 标题（必填）

- 取**第一个非空行**作为标题，其余行忽略。
- **≤ 55 字**（抖音上限约 55 字，取两平台交集；B站单独可到 80 字）。
- 不要带换行、不要带 `#话题`（话题交给 tags）。

### desc.txt — 简介（建议）

- **整个文件**作为简介，多行原样保留（B站简介支持多行排版）。
- B站上限约 2000 字；抖音端简介显示很短，**前 1~2 行放最重要的信息**。
- 不写则自动用标题代替（B站简介为必填项）。

### tags.txt — 标签（建议）

- 一行或多行都行；逗号 `,`、中文逗号 `，`、空格、换行都视为分隔符。
- **≤ 12 个，每个 ≤ 20 字**（B站限制；抖音话题建议 3~6 个）。
- 不需要 `#` 前缀。

### 封面图（建议，三张都可省略）

| 文件名 | 用途 | 比例 | 建议尺寸 |
|---|---|---|---|
| `cover-douyin.jpg` | 抖音封面 | **3:4 竖版** | 1080×1440 |
| `cover-bilibili.jpg` | B站封面 | **16:10 横版** | 1280×800（≥1146×717） |
| `cover.jpg` | 通用回退 | 任意 | 上面缺失时两个平台都用它 |

- 支持 `.jpg .jpeg .png`。
- 查找顺序：`cover-<平台>.<ext>` → `cover.<ext>` → 目录里唯一的图片文件。
- 目录里有多张图但不按上述命名 → 报错（防止用错封面）。
- 一张都不提供 → 平台自动截帧，B站/抖音审核后也可再改。

### meta.txt — 发布参数（可选）

`key=value`，一行一个，`#` 开头是注释：

```ini
# B站分区号（发B站必填，除非命令行 --tid）
tid=24
# 定时发布，两个平台同时生效；不写 = 立即发布
schedule=2026-10-14 12:00
# 非 default 账号时改（对应 sau login --account 的名字）
douyin_account=default
bilibili_account=default
# 本次跳过某平台
douyin=0
bilibili=0
```

常用 B站分区 tid：`24` 动画·综合、`27` 动画·MAD·AMV、`232` 生活·日常、
`23` 动画·手书。完整表搜「bilibili tid 分区表」，以 B站投稿页为准。

## 3. 平台字段对照（sau CLI 实际支持）

| 标准文件 | 抖音 | B站 |
|---|---|---|
| video | `--file` | `--file` |
| title.txt | `--title`（必填） | `--title`（必填） |
| desc.txt | `--desc` | `--desc`（必填，缺省用标题） |
| tags.txt | `--tags`（话题） | `--tags`（≤12） |
| cover-*.jpg | `--thumbnail`（3:4） | `--thumbnail` |
| meta.txt tid | — | `--tid`（必填） |
| meta.txt schedule | `--schedule` | `--schedule` |

未接入的 sau 能力（需要时再扩展）：抖音带货链接 `--product-link`、
声明 `--declaration`、合集 `--collection`。

## 4. 给 AI 的生成提示词（直接复制使用）

把下面的提示词 + 视频内容说明发给 AI（如 Claude），让它产出整个发布包：

````
请为一个视频生成多平台发布素材，严格按以下文件格式输出，我会逐个保存：

1. title.txt — 视频标题，一行，不超过 55 个汉字，有吸引力但不标题党，不含 # 号。
2. desc.txt — 视频简介，3~6 行：第 1 行一句话概括看点（最关键，抖音只显示前几行），
   后面补充内容亮点和引导关注。总长 200 字以内。
3. tags.txt — 8~10 个标签，逗号分隔，不含 # 号；前 3 个放最精准的主题词，
   后面放流量大词（如 动漫、新番、童年回忆）。
4. meta.txt — 只有一行：tid=<根据内容选最合适的 B站分区号>。

视频信息：
- 内容：<在这里描述视频内容 / 粘贴字幕或大纲>
- 目标观众：<如 动漫爱好者、亲子>
- 风格：<如 温馨治愈 / 搞笑解说>
````

封面图建议用视频关键帧 + 大字标题制作：抖音版裁 3:4、B站版裁 16:10，
分别存为 `cover-douyin.jpg` / `cover-bilibili.jpg`。

## 5. 校验与发布

```bat
bat\publish_dual.bat publish\<slug> --dry-run     只打印将执行的命令，检查字段
bat\publish_dual.bat publish\<slug>               正式发布（按 Y 确认）
```

目录不合规（缺视频、缺标题、图片歧义、B站缺 tid）会在发布前直接报错，不会发出半截。
