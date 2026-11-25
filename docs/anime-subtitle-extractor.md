# 日漫硬字幕工具：阶段 0 验证说明

当前只交付 [PRD](../new.md) 的**阶段 0——视频解码与 PTS 帧索引**。
不会裁剪、检测、分组、OCR、翻译或生成 SRT，也不接入现有下载 / ASR 流水线。
Windows EXE 和阶段恢复功能留到后续阶段；先用 Python 命令验证。

## 开发交接（2026-11-25）

**交接结论：阶段 0 已实现，阶段 1～5 尚未实现。** 当前能从视频得到原始帧及可追溯的
PTS 时间轴，还不能提取日文硬字幕；不要把阶段 0 的元信息 JSON 当作完整字幕结果 JSON。

| 功能 | 当前状态 |
| --- | --- |
| 视频逐帧解码、帧编号 | 已实现 |
| PTS、持续时间、CFR/VFR 检查 | 已实现 |
| 原始帧、索引和异常日志输出 | 已实现 |
| 指定字幕区域裁剪 | 未实现 |
| 灰度、二值化结果输出 | 未实现 |
| 字符候选框检测与输出 | 未实现 |
| 相邻帧相似度、连续分组 | 未实现 |
| 最佳帧选择、日文 OCR | 未实现 |
| SRT / 完整结果 JSON | 未实现 |

### 回家接续入口

- 开发分支：`fix/doraemon-materials-default`。
- 阶段 0 实现提交：`5b89a2e`（`feat(subtitles): add stage-zero video decoding and PTS frame index`）。
- 完整需求：[new.md](../new.md)，后续按其中的阶段顺序推进。
- 实现入口：`src/creator_agent/subtitle_extractor/cli.py`（命令行）、
  `analysis.py`（逐帧解码与输出）、`timeline.py`（PTS / duration / CFR/VFR）。
- 单元测试：`tests/unit/test_subtitle_analysis.py`、`tests/unit/test_subtitle_timeline.py`。

另一台机器在仓库根目录、确认工作区没有未提交改动后执行：

```bash
git fetch origin
git switch fix/doraemon-materials-default
git pull --ff-only origin fix/doraemon-materials-default
uv sync --extra subtitle-extractor
uv run --extra subtitle-extractor anime-subtitle-extractor --help
```

先按下文“回家实际验证”用真实短视频验收阶段 0。现有单元测试使用模拟解码器，
不代替真实视频的解码兼容性、画面正确性、VFR 和性能验证。

### 下一步开发范围

真实视频验收通过后，先实现 **阶段 1：指定字幕区域裁剪和预处理输出**：

1. 支持百分比 / 像素坐标及裁剪放大；初始区域可试 X 5%～95%、Y 72%～97%，
   但必须按实际视频确认，不能当作所有视频的固定答案。
2. 输出 `crops/`、`crop_config.json`、`crop_preview.jpg`；裁剪结果与原始帧编号一一对应。
3. 输出原始裁剪图、灰度、CLAHE、OTSU、自适应二值化等处理版本，便于人工比较。
4. 验收裁剪位置、图像尺寸一致性、帧编号映射，以及字幕没有被截断。

阶段 1 验收后再推进：阶段 2 字符候选框 / 字幕存在性检测 → 阶段 3 相似度与连续分组
→ 阶段 4 最佳帧和日文 OCR → 阶段 5 SRT / `result.json`。
本次只记录交接，不实现这些后续功能。

**接续约束：** 保留原始 PTS、可追溯回退和异常日志；不能用平均 FPS 重建 VFR 时间轴。
字幕结束时间取末帧 `pts_time + duration`。不覆盖已有任务输出，不提前引入翻译 / ASR，
不接入现有下载流水线；Windows EXE 和阶段恢复仍留待后续。

## 安装与运行

在仓库根目录执行（Python ≥3.12、uv）：

```bash
uv sync --extra subtitle-extractor
uv run --extra subtitle-extractor anime-subtitle-extractor --help
uv run --extra subtitle-extractor anime-subtitle-extractor --input "D:/Anime/episode01.mp4"
```

macOS/Linux 同样适用，只需换输入路径。工具使用 PyAV 解码**第一个视频流**，
按解码后的显示顺序逐帧编号；音频、已有软字幕和其他视频流不处理。
不会重新编码或修改输入视频。

默认任务目录为当前工作目录下的 `output/<视频文件名，不含扩展名>/`。
默认保存每帧 PNG，长视频可能需要大量磁盘空间；建议先用 30～60 秒的现成测试片段。

只检查时间轴、不保存图片：

```bash
uv run --extra subtitle-extractor anime-subtitle-extractor --input "D:/Anime/episode01.mp4" --output "output/episode01_index" --no-save-frames
```

指定输出目录，保留所有帧：

```bash
uv run --extra subtitle-extractor anime-subtitle-extractor --input "D:/Anime/episode01.mp4" --output "output/episode01_frames"
```

**输出目录必须不存在或为空。** 同名视频、重复运行、失败后的重试都应指定新的
`--output`；工具不覆盖已有结果，不提供 `--force`。一次失败会保留已经写出的数据，
但失败任务的索引可能不完整，不能当作成功结果使用。阶段 0 不支持断点恢复。

## 输出文件

```text
output/episode01_frames/
├── video_info.json              # 元信息、实际解码帧数、时间轴统计与异常计数
├── frame_index.csv              # 每帧唯一记录，包含时间数据来源
├── frames/                      # --no-save-frames 时不创建
│   ├── 000000.png
│   ├── 000001.png
│   └── ...
├── timestamp_anomalies.jsonl     # 按帧记录异常/回退；没有异常时为空
├── analysis.log                 # 开始、结束、异常与失败堆栈
└── task_status.json             # running / completed / failed / interrupted
```

`video_info.json` 中：

- `frame_count`：实际解码帧数，不是 `平均 FPS × 时长`。
- `expected_frame_count`：容器声明的帧数，只是参考，可能为空或不准确。
- `index_record_count`：实际写入索引的记录数；成功时必须等于 `frame_count`。
- `duration` / `duration_source`：优先视频流时长，其次容器时长，均未知时采用解码显示跨度。
- `first_pts` / `last_display_end` / `decoded_duration`：解码得到的原时间轴首帧时间、
  末帧显示结束时间、二者之差，可能与容器时长不同。
- `frame_rate_mode`：检查实际相邻 PTS 间隔后标记 `CFR` / `VFR`；少于 3 帧、缺失 PTS、
  重复或倒退 PTS 时保守标记 `unknown`。不能仅凭平均 FPS 判断。
- `timestamp_complete_rate`：有原始解码 PTS 的帧数 / 实际解码帧数。
- `pts_sources` / `duration_sources` / `anomaly_counts`：用于审计回退和异常。

## 时间轴约定

`frame_index.csv` 前四列保持 PRD 的定义：
`frame_index,image_path,pts_time,duration`。关闭帧保存时 `image_path` 为空，不伪造图片路径。

另外保留 `raw_pts`、`time_base`、`pts_source`、`duration_source`，以及 PTS 秒数和
持续时间各自的 `*_numerator` / `*_denominator`。内部按有理数计算；秒数列用 9 位小数
便于查看，后续需要无损读取时使用分子 / 分母列。

- 原始 PTS 秒数 = `raw_pts × time_base`，**不减掉首帧 PTS**。
- 非零起点、负数、重复和倒退 PTS 均保留原值；异常写入日志，不静默修正。
- 持续时间优先取解码器的正值，其次取下一帧的正 PTS 间隔。
- 末帧缺少持续时间时，可用已知视频流起点 + 时长推导结束时间；无法采用时，
  回退到最近有效原始 PTS 间隔，最后才用显式 FPS / 平均 FPS。
- 缺失首帧 PTS 时使用视频流起点，仍未知则以 0 为可追溯的回退起点；后续缺失 PTS
  优先使用上一帧持续时间，再用最近有效 PTS 间隔，最后才用 FPS。
- FPS 回退及其他推断会记录来源和异常；有缺失 PTS 的视频不会据此宣称是 CFR。
  无法可靠回退时直接报错，不造一个默认 25 FPS 时间轴。
- 字幕未来的结束时间应取末帧 `pts_time + duration`，而不是末帧 PTS。

其他时间参数：

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `--fallback-fps` | 无 | 仅在时序数据不足时明确指定 FPS 回退，优先于平均 FPS |
| `--gap-warning-seconds` | `1.0` | 超过此值的 PTS 间隔写提示；提示不等于已证明丢帧 |
| `--cfr-tolerance` | 自动 | CFR 间隔容差（秒），自动值考虑实际时钟精度并限制在最短间隔的 5% 内 |

## 回家实际验证

1. 先用 `--no-save-frames` 跑一次，确认命令正常完成。
2. 检查 `task_status.json` 的 `status` 为 `completed`，且
   `video_info.json` 的 `frame_count == index_record_count`。
3. 用同一段视频、不同输出目录保存 PNG，抽查首帧、中间帧和末帧：编号连续，
   文件与 `frame_index.csv` 一一对应，画面内容与原视频一致。
4. 查看相邻 `pts_time` 与 `duration`，对照播放器时间；末帧的显示结束时间不能少算一帧。
5. 检查 `timestamp_anomalies.jsonl`：缺失、重复、倒退、异常大间隔、
   帧持续时间与下一个 PTS 不一致、解码帧数与容器声明不一致，都有明确记录。
   单个末帧的 `inferred_duration` 可能只是正常回退，需要结合 `source` 判断。
6. 若有 VFR 视频，再对照它的实际间隔检查 `VFR` 判定；不要只看 `average_fps`。
7. 若报错或时间轴异常，反馈 `video_info.json`、`task_status.json`、
   `timestamp_anomalies.jsonl`、`analysis.log`，必要时附前后相关 CSV 行即可。

**仅 `completed` 不代表所有时间戳都正常**：成功解码但有异常的任务仍会保留原始数据，
终端会输出提示计数，必须查看异常内容。

## 开发侧代码测试（不处理视频）

```bash
uv run pytest tests/unit/test_subtitle_timeline.py tests/unit/test_subtitle_analysis.py
uv run ruff check src/creator_agent/subtitle_extractor tests/unit/test_subtitle_timeline.py tests/unit/test_subtitle_analysis.py
```

这些测试使用模拟解码器、帧和图片保存，不读取或生成真实视频，不需要安装 PyAV。
覆盖 PTS / VFR / 末帧时间、数据来源、逐帧文件映射、CLI、输出保护、异常与中断处理。
真实解码兼容性、画面正确性和处理性能仍需上述实际验证，反馈后再进入阶段 1。
