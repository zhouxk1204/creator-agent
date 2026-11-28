# 日漫硬字幕工具：阶段 0–5 实现说明

本工具实现 [PRD](../new.md) 的**阶段 0～5**：逐帧解码与 PTS 索引 → 字幕区域裁剪 → 字幕存在性检测 →
相似度分组 → 最佳帧 OCR → SRT / `result.json`。一次解码同时完成阶段 0～3，分组结束后才调用 OCR，
不做翻译、不接入现有下载 / ASR 流水线。

## 实现状态

| 功能 | 状态 | 代码入口 |
| --- | --- | --- |
| 逐帧解码、帧编号、PTS / duration / CFR-VFR 检查 | 已实现 | `analysis.py`、`timeline.py` |
| 原始帧、索引、异常日志 | 已实现 | `analysis.py` |
| 字幕区域裁剪（百分比 / 像素、缩放、四种变体） | 已实现 | `crop.py` |
| 字幕存在性检测（形态学 + 连通域打分） | 已实现 | `detection.py` |
| 相邻帧相似度、连续分组 | 已实现 | `grouping.py` |
| 同文合并、噪声过滤 | 已实现 | `extraction.py` |
| 最佳帧选择、OCR + 低置信度回退 | 已实现 | `extraction.py`、`ocr.py` |
| SRT / `result.json` / `groups.json` / 可视化 | 已实现 | `srt.py`、`extraction.py` |
| 翻译、字幕擦除、断点恢复、Windows EXE | 未实现（后续） | — |

## 安装与运行

```bash
uv sync --extra subtitle-extractor          # PyAV + Pillow
uv run --extra subtitle-extractor anime-subtitle-extractor --input "D:/Anime/episode01.mp4"
```

OCR 依赖体积大、且平台覆盖有限，单独安装（`pyproject.toml` 中有 `subtitle-ocr` extra）：

```bash
uv pip install paddlepaddle paddleocr        # 或 uv sync --extra subtitle-extractor --extra subtitle-ocr
```

注意：`paddlepaddle` 会把 numpy 解析到 2.3.5、pyyaml 到 6.0.2，装进现有环境会连带 downgrade；
本仓库的 `uv.lock` 保持原样，需要 OCR 的机器自行安装并在本地刷新 lock。

完整的日文字幕流程（默认参数）：

```bash
uv run --extra subtitle-extractor anime-subtitle-extractor --input episode01.mp4 \
  --output output/episode01 --ocr-language japan
```

只跑检测和分组、不识别文字（不需要 OCR 依赖，也没有 SRT）：

```bash
uv run --extra subtitle-extractor anime-subtitle-extractor --input episode01.mp4 \
  --output output/episode01_groups --no-ocr
```

**输出目录必须不存在或为空**；工具不覆盖已有结果，失败任务保留已写出的部分数据，但不支持断点续跑。

### Windows 一键启动

```bat
bat\subtitle\run_subtitle_srt.bat                        :: demo.mp4 + 中文 OCR
bat\subtitle\run_subtitle_srt.bat "D:\Anime\ep01.mp4"    :: 自动输出到 output\ep01_srt
bat\subtitle\run_subtitle_srt.bat "D:\Anime\ep01.mp4" japan
```

参数依次是输入视频、OCR 语言（默认 `ch`）、输出目录（默认 `output\<文件名>_srt`）。脚本会：
检查 OCR 依赖 → 拒绝覆盖已存在的输出目录 → 实时打印解码进度条和每组字幕的识别结果 →
结束后打印 `result.json` 摘要（分组数、SRT 条数、前 5 条字幕）→ 把 `analysis.log`
复制到 `logs\subtitle_<文件名>.log`。10 分钟片段大约需要 35～45 分钟。

如果默认输出目录里已经有 `crops\crop_config.json`（同一视频跑过一遍），脚本自动切换到**复用模式**：
跳过解码，只跑阶段 2～5，结果写到 `output\<文件名>_srt_ocr`，配置行会显示
`mode : Reuse crops ...`，通常十几分钟就能出新 SRT。

## 流水线

```
一次解码
  ├── 阶段 0：PTS / duration / 帧索引 → frame_index.csv、video_info.json
  ├── 阶段 1：固定区域裁剪 → crops/<frame>.png，可选四种变体
  ├── 阶段 2：字幕打分 → detection/frame_detection.csv
  └── 阶段 3：遮罩 IoU 比较 → 连续分组（组内留 9 个均匀分布帧）
        ↓ 分组关闭时
        阶段 4：抽 3 帧 OCR 投票 → ocr/group_XXXX.json
        ↓ 全部结束后
        阶段 5：同文合并 → 过滤 → result.srt、result.json、groups/groups.json、可视化
```

阶段 2～5 也可以不重新解码：已存在 `crops/` 时用 `--from-crops` 直接跑
检测 → 分组 → OCR → SRT（见下文「复用 crops」）。

### 阶段 2：字幕存在性检测

对裁剪图做：灰度 → 高斯模糊 → top-hat（15×9）突出亮色笔画 → 按 97 分位取阈值 →
开运算 2×2 → 连通域按高度 / 宽度 / 面积 / 填充率 / 长宽比过滤 → 按 y 中心聚成文字带 →
**按水平间距 1.5×字高把文字带断成若干「文字段」** → 每段单独打分，取最高分的段。

打分（0～1）：

```
score = 0.5 × min(1, 亮像素比例 / 0.45)
      + 0.3 × min(1, 掩膜密度 / 0.30)
      + 0.2 × min(1, 连通域数 / 6)
```

阈值默认 0.40。此外段宽必须 ≥ 裁剪宽度的 `coverage_gate`（0.06），避免把一小块亮斑当整行字幕。

两个改动解决了真实素材上的典型漏检：

- **旧的「取最宽文字带」会把字幕和右侧亮的背景连成一个带**，密度被稀释，整行掉到阈值下；
  现在横向断开、按段打分，字幕段自己胜出。
- **旧逻辑按带宽加总选带**，遇到脖子 / 衣服那种大面积亮区会把字幕带挤掉；现在同一帧的
  候选段全部打分，最高分入选，`000366-000378` 这种右侧短句也能命中。

代价是误报略多（草地、天空纹理特定帧仍可到 0.4-0.9 分），这些组大多 OCR 成空文本，被阶段 5 挡掉，
只多花一点 OCR 时间。**策略仍是宁可多检**：漏检的字幕补不回来，误检可以过滤。

### 阶段 2.5：OCR 兜底（`rescue_enabled`）

亮度检测对「青色 / 蓝色细字压在粉色、红色、黄色背景」几乎无响应，实测这类漏检帧的检测分常在
0.15-0.40 之间（帧里确实有字幕）。降阈值会把草地、鞋面等纹理一起放进来，于是改成**用 OCR 判定**：

- 只处理「检测分 ≥ 0.15 且未达阈值、但给了候选框」的帧（本片约 2351 帧，约 12 分钟）；
- 直接识别**整个裁剪带**（不是候选框——检测框经常只框住一个字的一根笔画，识别器需要整行）；
- 结果必须同时满足：文本有意义、置信度 ≥ 0.85、长度 ≤ 8、含文字字符、不是同一字符重复（`儿儿`）；
- 通过后按「该候选框所在水平带」生成掩膜参与分组，日志打印 `Rescued frame ...`。

实测：`0005306`（粉衣青字 `嗯?`）识别 0.97、`0010436`（`哦?`）1.00、`0017791`（`哦`）1.00 全部救回，
而 `0013007`（鞋）、`0001931`（草地）、`0003157`（脚）识别为空或 0.58 的低置信噪声，一律拒绝。

### 阶段 3：分组

- 与上一帧遮罩做 IoU，≥ `--similarity-threshold`（默认 0.85）视为同一组，否则关闭旧组开新组。
- 允许组内最多 `--max-missing-frames`（默认 2）帧没有检测到字幕，超过即关闭。
- 少于 `--min-group-frames` 帧的组丢弃（代码默认 1，CLI 默认 2）。
- 组内保留 `keep_frames`（9）个**在时间上分布开**的帧：新帧挤掉距离最近、清晰度较低的那一帧，
  而不是只留一串开场帧。这样组尾的帧也参与 OCR。

### 阶段 4：OCR

- 从保留帧里**均匀抽 `1 + --ocr-fallback-frames`（默认 3）帧**（首、中、尾），全部识别，不早停。
- **投票取字**：按归一化文本把几次识别聚成簇，票数最多的簇获胜，取簇内置信度最高那次的文本；
  同一句在三帧里读出 `走你` / `走你` / `走祢` 时，投票能消掉单帧的错字。
- 单组失败不中断整个任务，记 `ocr_error` 并继续；每组的候选结果和票数写在 `ocr/group_XXXX.json`。
- 语言用 PaddleOCR 代码：`japan` / `ch` / `en` / `korean`，也接受 `ja`、`zh` 等别名。
- **Windows 上必须关掉 oneDNN**：`paddle.set_flags({"FLAGS_use_mkldnn": False})` +
  `enable_mkldnn=False`，否则 PaddlePaddle 3.3 会抛
  `NotImplementedError: ConvertPirAttribute2RuntimeAttribute`。适配器已在 `ocr.py` 内处理。

### 阶段 5：同文合并、过滤与 SRT

- **文本清理**：去掉汉字之间的多余空格、字母两侧空格保留、句末单个 `.,;:`（省略号保留），
  `不需要100个铜锣烧...` 不会被截断。
- **相邻合并**：遮罩相似度会因字幕抖动、背景运动而掉到阈值下，把一句字幕拆成多条碎条；
  字幕淡入淡出时还会读出 `902给我看看啦` 这种带前缀噪声的变体。OCR 后把相邻、间隔 ≤
  `--merge-gap`（默认 0.8 秒）、文本相似度 ≥ `--merge-similarity`（默认 0.80）的组并成一条，
  起止时间取两端，**文本取置信度最高的那次识别**（噪声变体的置信度天然更低），合并次数记在
  `merged_groups`。查找「上一个有文本的组」时会跳过中间的空白组，因此漏检一两帧不会把一句话
  切成两条。
- **噪声过滤**：`groups.json` 里每条都有 `in_srt` 标记，决定是否进 SRT：
  - 文本不含任何文字字符（汉字 / 假名 / 字母 / 数字）→ 判 `no_text`，例如 `((`、`…。`；
  - 时长 < `--min-cue-duration`（默认 0.2 秒）→ 直接丢弃，这类多是场景切换帧上的单字噪声；
    例外：文本只有 1-2 个字的短句（`嗯`、`哦?`）门槛降到 0.05 秒，否则「只检出两三帧」的短句会被误杀；
  - `ok` 组满足时长后即进；
  - `low_confidence` 组还需置信度 ≥ 0.6、时长 ≥ 0.2 秒、文本 ≥ 2 字，否则视为误检。
- 时间：开始 = 组首帧 `pts_time`，结束 = 组末帧 `pts_time + duration`，不是末帧 PTS。
- 相邻重叠由 `build_srt` 裁到下一句开始；零长句补到 40 ms，不产生非法条目。
- SRT 使用 UTF-8 无 BOM，句序按时间排序并连续编号。

## 参数

阶段 0 / 1：

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `--crop/--no-crop` | 开启 | 关闭后只做阶段 0（且不能开 OCR） |
| `--crop-unit` | `percent` | `percent`（0-100）或 `pixel` |
| `--crop-box` | `5,72,95,97` | `x_start,y_start,x_end,y_end`，单位由 `--crop-unit` 决定 |
| `--crop-scale` | `1.0` | 裁剪图缩放，0.1～8.0 |
| `--crop-variants` | `gray,clahe,otsu,adaptive` | `none` 只留彩色裁剪，长片建议关掉变体省磁盘 |
| `--save-frames/--no-save-frames` | 开启 | 是否保存整帧 PNG |

阶段 2～5：

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `--ocr/--no-ocr` | 开启 | 关闭后只做检测与分组，不生成 SRT |
| `--ocr-engine` | `auto` | `auto` / `paddleocr` / `none`（`none` 等同 `--no-ocr`） |
| `--ocr-language` | `japan` | PaddleOCR 语言代码；中文片源用 `ch` |
| `--ocr-confidence-threshold` | `0.85` | 低于此值换候选帧重识别 |
| `--ocr-fallback-frames` | `2` | 额外候选帧数量 |
| `--detection-threshold` | `0.40` | 字幕存在性分数阈值 |
| `--similarity-threshold` | `0.85` | 同组所需遮罩 IoU |
| `--max-missing-frames` | `2` | 组内容忍的漏检帧数 |
| `--min-group-frames` | `2` | 少于该帧数的组丢弃 |
| `--merge-gap` | `0.8` | 相邻组参与合并的最大间隔（秒） |
| `--merge-similarity` | `0.80` | 合并所需文本相似度（忽略标点 / 全半角后比较） |
| `--min-cue-duration` | `0.2` | 短于此值的条目直接不进 SRT |
| `--from-crops` | 无 | 复用已有任务的 `crops/`，跳过解码，只跑阶段 2～5 |

## 复用 crops（推荐用于调算法）

阶段 0/1 的产物是完全自洽的：`crops/<frame>.png` + `frame_index.csv` 就够跑后半程。改检测或
OCR 参数后不必再花半小时解码：

```bash
.venv\Scripts\anime-subtitle-extractor.exe --from-crops output\demo_srt --output output\demo_srt_ocr --ocr-language ch
```

- `--from-crops` 指向旧任务目录（含 `crops/` 与 `frame_index.csv`），`--output` 必须是新的空目录；
- 只做 检测 → 分组 → OCR → SRT，产出与全量跑批一致（`detection/`、`groups/`、`ocr/`、`result.srt`）；
- 批量启动脚本 `bat\subtitle\run_subtitle_srt.bat` 已自动判断：默认输出目录里存在
  `crops\crop_config.json` 时，改为 `--from-crops`，并把结果写到 `output\<名称>_srt_ocr`，屏幕日志会打印
  `mode : Reuse crops ...`。同一个视频反复调参时省掉全部解码时间。

## 输出文件

```text
output/episode01/
├── video_info.json                  # 解码统计、时间轴、裁剪摘要、extraction 摘要
├── frame_index.csv                  # 每帧 PTS / duration 及来源
├── frames/                          # --no-save-frames 时不创建
├── crops/                           # 彩色裁剪；变体在 crops/<variant>/
├── crop_config.json                 # 实际区域、像素框、尺寸、变体参数
├── crop_preview.jpg                 # 抽 4 帧整帧红框 + 裁剪图
├── detection/frame_detection.csv    # 每帧 has_subtitle / score / 特征 / 清晰度
├── debug/similarity.csv             # 相邻检测帧的 IoU 与判定（same / new / start）
├── debug/detection_visualization.jpg
├── debug/groups_visualization.jpg   # 前 12 组的最佳帧、时间、文本
├── groups/groups.json               # 每组帧范围、时间、文本、置信度、状态、in_srt
├── groups/group_0001/frame_000123.png  # 组内保留帧（最多 9 张，时间上分布）
├── ocr/group_0001.json              # 该组每次 OCR 调用的文本、置信度、box 数
├── ocr/frames.json                  # 逐帧文本：有字的帧才写，空的不写
├── result.srt                       # 最终字幕
├── result.json                      # 配置、摘要与完整分组列表
├── timestamp_anomalies.jsonl        # 时间轴异常，正常时为空
├── analysis.log
└── task_status.json                 # running / completed / failed / interrupted
```

`result.json` 的 `summary`：`detected_frames`、`processed_frames`、`detection_rate`、`groups`、
`groups_with_text`、`srt_cues`、`merged_groups`、`ocr_calls`、`ocr_failures`。

`ocr/frames.json` 是逐帧清单：每条 `{frame, time, text, confidence, group_id, in_srt}`，只收录识别到文字的帧，
空帧不写；`text` 取该帧所在组的投票结果（同组各帧共享一句），所以文件同时能回答「哪一帧有哪一句」和
「哪些帧根本没被识别」。旧任务可用 `python scripts/frame_ocr_json.py <任务目录> [frame_index.csv]` 秒级重建。

## 时间轴约定

- 原始 PTS 秒数 = `raw_pts × time_base`，不减首帧 PTS；不按平均 FPS 重建 VFR 时间轴。
- 帧持续时间优先取解码器值，其次取下一帧 PTS 间隔，最后才用显式 / 平均 FPS；来源逐帧记录。
- 字幕结束时间取末帧 `pts_time + duration`。

## 验证记录

### 60 秒片段（`demo.mp4` 前 60 秒，中文硬字幕，`--ocr-language ch`）

- 1800 帧，检测 1114 帧（61.9%），37 组（合并前 73 组），25 组有文字，105 次 OCR；
- 合并把「喂大雄好好干啊」「什么啊不就是只鸟吗」等碎条并回单句，SRT 由 73 行降到 19 条；
- 噪声组（`红`、`1`、`om`、`心`，均为低置信度短碎片）被 `in_srt` 过滤挡掉；
- 残留一处 OCR 瑕疵：`2好疼`（前导字符），属于字幕淡入时截到半帧，非流程错误。

### 全片（`demo.mp4`，中文硬字幕，复用 `output\demo_srt\crops`）

- 18,718 帧，检测 12,366 帧（66.1%），611 组（合并前 679 组），258 组有文字，3,539 次 OCR；
- 174 条 SRT，字幕覆盖 326.3 秒；`analysis.log` 与 `result.json` 都在任务目录里；
- 复查用户指出的两处：
  - `000130-000167`（同一句「走你」）现在是**一条** `00:00:04,333 --> 00:00:05,600`，
    旧结果只有 131-133 帧、时间轴缺头少尾；
  - `000366-000378` 识别为 `嗯？`，`00:00:12,200 --> 00:00:12,633`，旧结果整段漏检；
- 噪声过滤后仍有极少数 OCR 瑕疵（如 `谁来救救我~CO` 尾部的 `CO`），属于单帧背景笔画被读进同一行，
  不影响时间轴。

## 已知限制

- 检测是基于形态学的启发式，不是模型；换片源、换字体、换描边都可能需要重调 `--detection-threshold`。
- 默认裁剪区是 1920×1080 日漫经验值，必须先用 `crop_preview.jpg` 确认字幕没被截断。
- 中文演示片只能验证链路与时间轴；日文 OCR 模型（`japan`）尚未在真实日文字幕上验收。
- 同一句 OCR 出错、文本不同的两条只有在相似度 ≥ `--merge-similarity` 时才会合并；差别太大仍会分裂。
- 不做时间轴平滑、不做字幕翻译、不做硬字幕擦除。

## 测试

```bash
uv run pytest tests/unit/test_subtitle_timeline.py tests/unit/test_subtitle_analysis.py \
  tests/unit/test_subtitle_crop.py tests/unit/test_subtitle_extraction.py
uv run ruff check src/creator_agent/subtitle_extractor tests/unit/test_subtitle_*.py
```

单测用模拟解码器与假 OCR 引擎，不读真实视频、不需要 PyAV / PaddleOCR。覆盖时间轴回退、裁剪区域与
变体、检测打分、分组状态机、同文合并、噪声过滤、低置信度回退、OCR 异常降级、SRT 格式化与重叠裁剪、
CLI 参数校验与输出保护。
