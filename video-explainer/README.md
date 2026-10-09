# video-explainer — 第一阶段：视频分镜切割

解说视频制作流水线的第一阶段：**视频 → 分镜检测 → 独立视频片段 → scenes.json**。

```text
原始视频
    ↓
PySceneDetect 分镜检测（ContentDetector）
    ↓
后处理：边界复核合并（闪烁误切修复）+ 黑场标记
    ↓
每个分镜的开始/结束时间（秒 + 帧号）
    ↓
FFmpeg 精确切割成独立视频
    ↓
scenes.json + postprocess_report.json
```

后续阶段（Qwen3-VL 视频理解、字幕匹配、解说稿生成、TTS、自动剪辑）都以
`scenes.json` 作为输入，本阶段不做这些事。

---

## 1. 安装依赖

Python 3.11+，然后：

```bash
cd video-explainer
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt   # scenedetect + opencv-python-headless
# （等价于 pip install "scenedetect[opencv]"，0.7+ 已拆成独立依赖）
```

## 2. 配置 FFmpeg

FFmpeg 使用系统安装版（**不要**用 pip 安装）。查找顺序：

1. 环境变量 `FFMPEG_PATH` / `FFPROBE_PATH`（指向 exe 完整路径）
2. 系统 `PATH`
3. 平台默认位置：
   - Windows: `C:\ai\ffmpeg\bin\ffmpeg.exe` / `ffprobe.exe`
   - macOS: `/opt/homebrew/bin`, `/usr/local/bin`
   - Linux: `/usr/bin`, `/usr/local/bin`

都找不到时会报出明确错误并列出所有检查过的位置。

## 3. 执行命令

**Windows（推荐）**：双击 `split_scenes.bat`，按提示输入（或拖入）视频所在文件夹，
自动取该文件夹下**第一个视频**进行切割；也可以直接把文件夹/视频文件拖到 bat 图标上。
首次运行会自动创建 venv 并安装依赖。如需覆盖已有输出，把 bat 顶部的
`EXTRA_ARGS=` 改为 `--overwrite`。

**命令行**：

```bash
python scripts/split_scenes.py "D:\videos\935#1.mp4"
```

默认输出到与视频同名的目录 `D:\videos\935#1\`。

## 4. 参数说明

| 参数 | 说明 |
|---|---|
| `--output, -o DIR` | 自定义输出目录 |
| `--threshold 27` | 覆盖 ContentDetector 阈值 |
| `--min-scene-len 12` | 覆盖最短分镜长度（单位：帧） |
| `--config PATH` | 指定配置文件（默认 `config/scene_config.json`） |
| `--no-export` | 只检测分镜、生成 scenes.json，不切割视频（先看效果再导出） |
| `--overwrite` | 输出目录已存在且非空时允许覆盖（**默认拒绝**） |
| `--merge-threshold 100` | 边界复核合并阈值：min_cross 低于该值的边界被撤销 |
| `--cross-window 6` | 边界复核时两侧各检查的帧数 |
| `--analysis-scale 0.5` | 后处理分析用缩放比例 |
| `--no-merge` | 关闭闪烁误切合并 |
| `--no-black-mark` | 关闭黑场标记 |
| `--dry-run` | 只生成 `postprocess_report.json` 分析报告，分镜结果保持检测原样 |

优先级：命令行参数 > `config/scene_config.json` > 内置默认值。

退出码：`0` = 全部成功；`1` = 流程错误（如 FFmpeg 缺失）；`2` = 有分镜导出失败。

## 5. 输出目录结构

```text
D:\videos\935#1\
├── scenes\
│   ├── 0001.mp4
│   ├── 0002.mp4
│   └── ...
├── scenes.json
├── postprocess_report.json
└── scene_split.log
```

**原视频永远只读**，不会被移动 / 删除 / 重新编码覆盖。

## 6. scenes.json 格式

```json
{
  "version": "1.1",
  "episode": "935#1",
  "source": {
    "file": "935#1.mp4",
    "duration": 643.7,
    "fps": 23.976,
    "width": 1920,
    "height": 1080,
    "codec": "h264"
  },
  "detector": {
    "type": "content",
    "threshold": 27.0,
    "min_scene_len_frames": 12
  },
  "postprocess": {
    "dry_run": false,
    "merge": { "enabled": true, "merge_threshold": 100.0, "cross_window": 6, "analysis_scale": 0.5, "merge_max_iterations": 4 },
    "black": { "enabled": true, "black_max_yavg": 20.0, "...": "..." },
    "original_scene_count": 180,
    "merged_boundary_count": 32,
    "black_scene_count": 1
  },
  "scenes": [
    {
      "scene_id": "0001",
      "start": 0.0,
      "end": 4.208,
      "duration": 4.208,
      "start_frame": 0,
      "end_frame": 101,
      "start_timecode": "00:00:00.000",
      "end_timecode": "00:00:04.208",
      "video": "scenes/0001.mp4",
      "black": false,
      "merge_history": [],
      "boundary_score": null,
      "review_status": "auto"
    }
  ]
}
```

- 时间一律是 **秒 + 浮点数**（毫秒级精度，供后续字幕对齐），另附格式化时间码。
- `video` 是**相对路径**，与机器无关。
- `scene_id` 为 `0001` / `0002` / …，与文件名 `0001.mp4` 一一对应。
- `black`：纯黑场标记。**只标记不删除**，分镜照常导出、留在时间轴上（字幕匹配依赖时间轴完整）。
- `merge_history`：被边界复核合并进来的原始分镜（检测编号、边界帧、min_cross、原因）。
- `boundary_score`：本分镜起始边界的 min_cross 复核分数（首个分镜为 `null`）。
- `review_status`：`auto` = 自动处理；人工确认后由下游改写。

## 6.1 后处理：闪烁误切合并 + 黑场标记

动画中的明暗交替特效（爆炸/电击/闪回，约 3~4 帧一次亮度脉冲）会让
ContentDetector 在**同一个镜头内部**反复误切，切出一堆 0.4~1.4 秒的碎分镜。
后处理对每个候选边界做复核：取边界**两侧各 `cross_window` 帧**两两比较
（半分辨率 HSV mean-abs-diff），取最小值 `min_cross`——闪烁段两侧必存在
相近帧（min_cross 小），真正的切换两侧画面完全不同（min_cross 大）。
低于 `merge_threshold` 的边界被撤销，相邻分镜合并；迭代复查直到收敛。

黑场判定综合 4 个指标（平均亮度、亮度标准差、黑像素占比、黑帧比例），
避免把暗夜场景、黑底字幕卡误判为纯黑场。黑场**照常导出并保留在时间轴上**，
只在 scenes.json 里标记 `black: true`，由下游决定如何使用。

所有阈值都在 `config/scene_config.json` 的 `postprocess` 段可调；
`merge_threshold` 只在当前 min_cross 度量口径（半分辨率 + HSV 三通道
mean-abs-diff 之和）下有效，改算法必须重新标定。

每次运行生成 `postprocess_report.json`：逐边界的复核分数与决策（KEEP/MERGE）、
合并迭代记录、黑场判定明细，用于复盘。调阈值时建议先 `--dry-run` +
`--no-export` 看报告，确认无误后再正式跑。

## 7. 如何调整 threshold

```text
threshold 越低 → 越容易检测到切换 → 分镜更多（可能把淡入淡出、闪光也切开）
threshold 越高 → 需要更明显的画面变化 → 分镜更少（可能漏掉缓变切换）
```

`min_scene_len`（单位：帧，按实际 FPS 换算，24fps 下 12 帧 = 0.5 秒）：

```text
min_scene_len 越小 → 可以产生更多短镜头（连续闪切会被切成独立分镜）
min_scene_len 越大 → 过滤更多短镜头（闪切被合并进相邻分镜）
```

建议先用 `--no-export` 只检测、检查 `scenes.json` 中的分镜数量和时长分布，
满意后再去掉 `--no-export` 导出视频。

## 8. 常见问题

**Q: `[ERROR] ffmpeg not found`** — FFmpeg 不在 PATH 也不在上述默认位置。
设置环境变量 `FFMPEG_PATH=C:\ai\ffmpeg\bin\ffmpeg.exe`（ffprobe 同理），
或把 `C:\ai\ffmpeg\bin` 加入 PATH。

**Q: 输出目录已存在报错** — 默认不覆盖已有结果。确认要重新生成时加 `--overwrite`。

**Q: 切出来的分镜边界不准？** — 切割使用 `-ss/-to` 前置 + 重新编码（CRF 18，
保持原始分辨率和 FPS），边界是帧级准确的；没有用 `-c copy`（会吸附到关键帧）。

**Q: 整个视频只检出 1 个分镜？** — 视频没有明显切换时会返回整段作为唯一分镜，
这是正常行为。如果明明有切换，降低 `--threshold` 试试。

**Q: 日志在哪里？** — 输出目录下的 `scene_split.log`，包含每个分镜的时间码、
每次导出/校验结果；报错的完整 traceback 也在这里（控制台只显示简明错误）。

## 9. 测试

```bash
pytest
```

测试视频由 ffmpeg lavfi 现场生成（纯色硬切），覆盖：正常切割、无切换视频、
2 秒短视频、输出目录冲突、FFmpeg 缺失报错、23.976fps 保留、min_scene_len 过滤闪切、
闪烁误切合并、关闭合并、黑场标记（仍导出）、dry-run 报告。
