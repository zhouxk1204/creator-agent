# 人物素材收集工具 V1.0 使用手册

> 用途：从已切好的分镜中自动提取人物候选帧 → 人工看图分类 → 生成可追溯的素材索引。
> 为后续角色 JSON / Qwen3-VL 人物分析做准备。**本工具不修改分镜切割流程，只读取它的产出。**

文档位置：`docs/character_collection_guide.md`
入口脚本：`scripts/collect_character_frames.py`

---

## 一、前置条件（每集只需做一次）

先有分镜结果。如果某一集还没切分镜，先跑已有的分镜切割（这一步是老流程，没变）：

```bash
uv run python scripts/split_scene.py "output/935_1.mp4"
```

跑完应该有：

```
output/
├── 935_1.mp4                    # 故事视频（抽帧来源）
└── 935_1_scenes/
    ├── scenes.json              # 分镜清单（本工具的输入）
    ├── 001.mp4 002.mp4 ...      # 分镜视频（本工具不用，别删）
    └── contact_sheet.jpg
```

**命名约定**：源视频 `935_1.mp4` → 剧集 ID（episode_id）就是 `935_1`（文件名 stem）。

## 二、标准操作流程（每集 4 步）

### 第 1 步：预览抽帧计划（不改任何文件）

```bash
uv run python scripts/collect_character_frames.py --episode 935_1 --preview
```

会列出每个分镜的时长、计划抽几张、采样时间点。确认无误再下一步。

### 第 2 步：提取候选帧 + 生成联系表

```bash
uv run python scripts/collect_character_frames.py --episode 935_1
```

产物：

```
character_library/
├── inbox/935_1/                         # 通过质量过滤的候选图（原始参考图）
│   ├── 935_1_V003_T001240_C01.jpg
│   └── ...
├── previews/935_1/                      # 联系表（只看不用，20 张/页）
│   ├── contact_sheet_001.jpg
│   └── contact_sheet_002.jpg
└── manifests/935_1_candidates.jsonl     # 索引：来源 + 时间戳 + 过滤原因 + 分类结果
```

日志会给出：计划抽帧数、通过数、黑场/闪白/纯色/模糊/重复各过滤多少、哪些分镜全军覆没。

### 第 3 步：人工分类（手动，复制不是移动）

1. 打开 `character_library/previews/935_1/contact_sheet_001.jpg` 逐页浏览。
2. 看中某张 → 按联系表上的编号（如 `V003_T001240_C01`）到 `inbox/935_1/` 找到原图 `935_1_V003_T001240_C01.jpg`。
3. **复制**到对应角色文件夹：

```
character_library/characters/
├── doraemon/        哆啦A梦
├── nobita/          大雄
├── shizuka/         静香
├── gian/            胖虎
├── suneo/           小夫
├── dorami/          哆啦美
├── nobita_mother/   大雄妈妈
├── nobita_father/   大雄爸爸
├── gian_mother/     胖虎妈妈
├── suneo_mother/    小夫妈妈
├── teacher/         老师
└── unknown/         确认不了是谁的先放这里
```

注意：

- **一定是复制，不要移动**——inbox 原图要留着做追溯。
- **文件名一个字都不要改**，索引靠文件名关联。
- 不确定的放 `unknown/`；完全不清晰的直接不选，留在 inbox 即可。

### 第 4 步：同步分类结果到索引

```bash
uv run python scripts/collect_character_frames.py --episode 935_1 --sync-labels
```

输出：`新分类: N   已一致: M   已确认总数: K / 总数`，有异常会逐条警告（见第五节）。

## 三、候选图片文件名怎么读

```
935_1_V003_T001240_C01.jpg
│     │    │        │
│     │    │        └─ C01: 该分镜内第 1 张候选
│     │    └─ T001240: 时间戳 = 故事视频内第 1240 毫秒（从故事视频 0:00 算）
│     └─ V003: 分镜编号，对应 scenes.json 里的 "id"
└─ 935_1: 剧集 ID = 源视频文件名
```

拿着文件名就能回到 `scenes.json` 找到分镜，再用时间戳在故事视频里定位画面。

## 四、质量过滤规则（会自动跳过这些图）

| 过滤 | 判定 | 阈值位置 |
|---|---|---|
| 黑场 | 画面平均亮度 < 12 | `frame_filter.py: BLACK_MEAN` |
| 闪白 | 平均亮度 > 243 | `WHITE_MEAN` |
| 纯色 | 颜色标准差 < 12（雾、渐变也可能触发） | `SOLID_STD` |
| 模糊 | Laplacian 方差 < 40 | `BLUR_VAR_MIN` |
| 重复 | 与同分镜已保留帧相似度 ≥ 0.995 | `DUP_SIM` |

- 被过滤的图**不会保存**，但索引里保留记录和原因（`filter_reason` 含实测值）。
- 某分镜全被过滤 → 日志单独列出，不会静默丢弃。
- 阈值太松/太紧：改 `src/creator_agent/character_collection/frame_filter.py` 顶部常量后重跑提取即可（已确认的人工分类不会丢）。

抽帧数量规则在 `frame_sampler.py`：≤1s→1 张，≤4s→3 张，≤8s→4 张，>8s→5 张，分镜内均匀采样并避开首尾 0.15s。

## 五、安全规则与异常警告

**重复运行是安全的**，随时可重跑：

- 重跑提取：图片重新生成（内容相同），索引合并——**已确认的人工分类永不覆盖**；如果某条已确认记录在新提取中没出现，会保留并警告。
- 重跑同步：已一致的跳过，只更新新分类的。

`--sync-labels` 的警告类型（都只警告、不动数据）：

| 警告 | 含义 | 怎么处理 |
|---|---|---|
| `同时出现在 X, Y，保留原标签` | 同一张图复制进了两个角色目录 | 删掉多余的副本再同步 |
| `已确认=X，文件夹=Y` | 文件被挪到别的角色目录，但索引已确认过 | 同步不会自动改标签，需手动改（见本节下方） |
| 不可解析的文件名 | characters/ 里混进了改名/无关图片 | 改回候选 ID 文件名或移走 |
| 属于其他剧集 | 把 936 集的图放进来同步 935 | 正常现象，换对应 --episode 同步 |
| 文件不在 characters/ 中 | 索引已确认但文件被删了 | 从 inbox 重新复制回去 |

想推翻某条已确认的分类：删除 `characters/<旧角色>/` 里那份文件 → 复制进新角色目录 → 手动把 `manifests/935_1_candidates.jsonl` 里该行的 `review_status` 改回 `"unreviewed"`、`character_id` 改为 `null` → 再 `--sync-labels`。（V1.0 故意不提供自动改标签，防止误操作。）

## 六、常见问题

**Q：提示「找不到故事视频」？**
A：默认按 `output/<episode>_scenes/scenes.json` 的同级目录找 `935_1.mp4`。视频在别处就显式指定：
```bash
uv run python scripts/collect_character_frames.py --episode 935_1 \
    --scenes-json "D:/path/935_1_scenes/scenes.json" --video "D:/path/935_1.mp4"
```

**Q：提示「--episode 与 source 推导不一致」？**
A：以 scenes.json 里的 source 文件名为准，检查是不是 --episode 写错了。

**Q：个别分镜解码失败？**
A：只影响那个分镜（索引里标 `decode_failed`），其他分镜照常。日志会列出。

**Q：联系表上的字是乱码/方框？**
A：不会——标签只用英文数字（候选 ID + 质量状态），不依赖日文字体。完整信息看 jsonl 索引。

**Q：想换一集处理？**
A：把命令里的 `935_1` 全部换成新剧集 ID，4 步重来。各集 inbox/previews/manifests 互相独立；`characters/` 是所有集共用的。

## 七、首次使用建议（验收）

不要一上来跑整部片库。先选 1 集里 30–50 个分镜，覆盖：短镜头、长镜头、多人同框、暗场、闪白、快速动作。核对：

- [ ] 联系表能正常打开，编号和 inbox 图片对得上
- [ ] 黑场/重复图被过滤，日志里有统计
- [ ] 随便抽几张图，用文件名的时间戳在故事视频里能找到对应画面
- [ ] 复制几张到角色目录 → `--sync-labels` 显示新分类数量正确
- [ ] 重跑提取 → 已确认分类还在
- [ ] 重跑同步 → 显示「已一致」

验收过后，后续才接 Qwen3-VL 人物分析（读 `characters/` 目录生成待审核角色 JSON）。
