# 01_download_latest_materials.bat — 最新话 Doraemon 素材下载

本文对应 [Windows 启动器](./01_download_latest_materials.bat) 的实际流程。macOS/Linux 的 [最新话doraemon素材下载.sh](./最新话doraemon素材下载.sh) 仍是简化的串行流程，差异见文末。

## 1. 主线与并行分支

Windows 默认 `DOWNLOAD_VIDEO=1`，双击后直接回车：

```text
先检查上次未完成剧集；没有待恢复任务才查询最新已播出集号和日期
                    ↓
创建/复用素材目录，记录 materials 标记与 pipeline_state.json
                    ↓
         同时启动两条分支

视频主线                              资料分支
TVer 下载整集                         抓取标题、日文简介、版权
    ↓                                下载各故事封面
分割故事视频                              ↓
    ↓                                ComfyUI 图片放大
对 _1、_2 依次执行 Real-ESRGAN 超分        ↓
每段结束立即清理临时帧                  更新 metadata.image_file
                                     放大成功后删除原图

         等待资料分支结束，报告主线结果与资料警告
```

**资料抓取和图片放大不再是视频下载的前置依赖。** 两条分支都直接写入同一个素材目录，但不写同一文件。子进程输出带时间戳和阶段标签实时显示在控制台，并追加保存到日志。分割阶段保留标准输入，仍可输入 `y` 确认。

恢复时使用本机保存的集号、播出日期和素材目录，不查询最新故事索引。没有待恢复任务时，只请求故事索引解析最新已播出集号和日期，不等故事页、封面下载或 ComfyUI 启动；播出日期直接传给 TVer，不从可能陈旧的 `.last_fetched_date` 读取。

资料抓取失败只显示警告，不中断下载、切分或超分，也不改变视频主线的退出码，但整集仍标记为未完成，方便下次补抓。普通视频失败或用户取消切分时，已经启动的资料分支仍会完成；启动器退出前等待它结束。Ctrl+C 中断会停止正在运行的子进程，保留已完成视频。

## 2. 运行模式

| 操作 | 执行内容 |
| --- | --- |
| 双击后直接回车 | 优先恢复上次未完成剧集；没有待恢复任务时处理最新已播出话 |
| 输入 `--latest` 或传入 `01_download_latest_materials.bat --latest` | 忽略待恢复记录，处理最新已播出话 |
| 顶部设置 `DOWNLOAD_VIDEO=0` 后直接回车 | 最新已播出话资料抓取和图片放大；跳过所有视频步骤 |
| 顶部设置 `UPSCALE_VIDEO=0` 后直接回车 | 照常下载 + 分割，跳过 Real-ESRGAN，只留分割片段检查结尾 |
| 输入集号或传入参数，如 `01_download_latest_materials.bat 941` | 仅抓取指定话日文资料和封面 |

仅资料模式不会删除或重新处理目录中的已有视频。最新话模式会保存素材目录状态；指定集号查询模式不会覆盖 `logs/.last_doraemon_materials`。仅资料模式和指定集号模式的抓取失败仍返回非零退出码。

本入口**不负责**日中标题/简介翻译、标签生成、日语 ASR、字幕矫正/重翻、剪映导出或视频发布。标题/简介翻译与标签生成移至 `06_correct_subtitles` 的字幕处理前置步骤，见 [06 使用说明](./06_correct_subtitles.md)。`02_fetch_doraemon` 和每周任务保留独立翻译能力。

## 3. 启动与依赖

启动器调用 `bat/_bootstrap.bat`，定位仓库、切换工作目录、检查 `.venv`；缺少环境时执行 `uv sync`，并提供 `%PY%`。Bootstrap 失败立即停止。

最新话模式调用：

```bat
"%PY%" scripts\download_doraemon_materials.py
```

仅资料模式追加 `--no-video`。Python 调度器管理资料子进程和视频阶段的退出码，不使用 batch 轮询或固定共享暂存目录。

| 依赖 | 用途 / 默认位置 |
| --- | --- |
| 仓库 Python `.venv` | 资料抓取、TVer 下载、故事切分、并行调度 |
| `config/settings.yaml` | 下载/切分使用的 ffmpeg 等设置 |
| ComfyUI workflow | `C:\Users\34696\Documents\ComfyUI\user\default\workflows\doraemonn_cover.json` |
| ComfyUI Core | `C:\agents\ComfyUI`，没有现有服务时尝试自动启动 |
| Real-ESRGAN | `C:\to4k\Real-ESRGAN` |
| 超分 Python | `C:\Users\34696\miniconda3\envs\upscale\python.exe` |
| 超分模型 | `weights\2x_AniScale2S_Compact_i8_60K.pth` |
| 超分 ffmpeg | Real-ESRGAN 目录下的 `ffmpeg.exe`，需要 CUDA / NVENC |

第三方超分引擎、模型和 conda 环境不包含在本仓库。并行运行时图片与视频超分可能同时使用 GPU，实际显存余量和运行速度需在 Windows 机器确认。

## 4. 最新集选择和素材目录

从 TV Asahi 的 `doraemon/json/story.json` 读取集号和 ISO 日期，只保留：

```text
播出日期 ≤ 当前日本时间（JST）日期
```

选择日期最新的一集；已公布但未播出的下一话不参与。最新话模式所有资料和视频输出到：

```text
%USERPROFILE%\Desktop\doraemon_<非补零集号>\
```

第 `0941` 话使用 `doraemon_941`。在启动两个分支前原子写入：

```text
仓库\logs\.last_doraemon_materials → 桌面素材目录的绝对路径
```

即使后续下载、切分或超分失败，目录状态也保留。`04_ja_asr`、`06_correct_subtitles` 和发布入口可继续复用该目录；`logs/` 为本机状态，不随 Git 同步。

完整视频流程额外保存 `logs/.last_doraemon_pipeline`，指向上次完整流程的目录，目录内的 `pipeline_state.json` 原子记录集号、ISO 播出日期、当前阶段、状态和更新时间。仅资料模式不会覆盖这个独立标记，避免抓取最新话资料后丢失之前的视频恢复入口。旧版本只有 materials 标记时，兼容读取它指向的 pipeline 状态。

`running`、`failed`、`interrupted` 和 `cancelled` 均视为待恢复。只有视频主线与资料子进程均成功结束才标记 `completed`。关闭窗口后残留的 `running` 也可恢复；损坏或缺失的记录会回退到最新话解析，损坏记录同时显示警告。显式 `--latest` 会将独立标记切换到最新话，不自动追踪更早的未完成任务。

## 5. 资料分支：标题、简介、封面与图片放大

确定集号后启动独立子进程：

```bat
"%PY%" -u scripts\fetch_doraemon.py 941
```

### 抓取输出

故事页提供总标题、放送日、各故事日文标题/简介、版权和图片 URL。资料写入素材目录内的 `metadata.json`（schema v2），包括补零集号、故事 ID/序号、日文来源字段和本地封面文件名 `image_file`。

初次抓取时 `zh` 和发布标题为 null，平台标签为空数组；`publish.video_path` 默认 `<story_id>_final.mp4`。重新抓取保留既有中文、人工发布配置和未知字段；日文标题/简介变化会使旧译文过期。标题去掉 `「」`，简介中的引号和换行保留。远程 `image` 地址只用于下载，不写入 metadata，也不生成简介 Markdown。完整字段见 [metadata 标准](../../docs/publish-package.md)。

### 图片替换与重跑

每个故事独立处理，例如：

```text
下载 941_1.jpg
    ↓
ComfyUI 成功生成 941_1_4k.jpg
    ↓
原子更新 metadata：image_file = "941_1_4k.jpg"
    ↓
删除 941_1.jpg
```

- 放大失败或 ComfyUI 不可用：保留原图，metadata 继续指向原图。
- 先保存完整的放大图和 metadata，再删除原图；metadata 写入失败不删除原图。
- 重跑时复用已有的非空放大图，不重新下载已删除的原图，也不启动 ComfyUI；metadata 修正为放大图文件名，并清理仍遗留的原图。若原图比放大图更新，则旧放大图不视为完成，保留原图并重试放大。
- `fetch_doraemon.py 941 --force` 强制重新下载、放大；本次放大失败时保留本次原图作为封面，下一次普通重跑继续放大新原图，不会切回旧封面并删除新原图。
- 图片下载和 ComfyUI 输出通过临时文件原子替换，避免中断留下半张图被重跑误判为完成。
- 扩展名沿用源图片，不保证一定是 `.jpg`；`_4k` 是命名约定，不保证精确 4K 分辨率。

ComfyUI 查找顺序：`COMFYUI_URL` → `127.0.0.1:8000` → `127.0.0.1:8188` → 尝试启动本机 Core。只关闭脚本自己启动的服务，不关闭用户已有的 ComfyUI。

资料子进程输出实时显示并追加到素材目录的 `materials_log.txt`，每次重跑保留旧日志。脚本自己启动 ComfyUI 时还会写 `仓库/logs/comfyui_server.log`。单个日志文件不可写时显示警告，继续显示控制台输出，不阻止视频流程或资料抓取。

成功抓取后更新 `logs/.last_fetched` 和 `logs/.last_fetched_date`；日期无法解析时清除旧日期状态。独立使用 `fetch_doraemon.py --resolve-latest` 可只解析最新集并写这两个状态，不抓取图片或故事页。Windows 视频主线不依赖这些状态文件。

## 6. 视频主线

### 6.1 TVer 下载

```bat
"%PY%" scripts\download_tver.py ^
  --out-dir "%USERPROFILE%\Desktop\doraemon_941" ^
  --name 941 --broadcast-date 2026-11-07
```

默认系列 ID 为 `srtsxzl3si`。按故事索引的播出日匹配 TVer 的 `broadcastDateLabel`，避免 TVer 更新滞后时将上一话保存为新集号。现有匹配只比较月日，不比较年份。

yt-dlp 使用 8 个并行 HLS fragments，元数据/fragment 各重试 10 次，进程内解密并 remux 为 MP4。只保存 `941.mp4`，不生成 TVer 缩略图或 info-json；下载中的 `.part` 由 yt-dlp 管理，历史 sidecar 文件不自动删除。

重跑时，已命名的整集 MP4 达到 50 MiB 就复用，下载脚本在请求 TVer API 前跳过，`--force` 可禁用此检查。

下载失败不继续分割和超分。没有可用节目而下载脚本未生成视频时，后续分割会失败；资料分支不受影响。

### 6.2 故事分割

```bat
"%PY%" scripts\split_episode.py ^
  "%USERPROFILE%\Desktop\doraemon_941\941.mp4" ^
  -o "%USERPROFILE%\Desktop\doraemon_941" --confirm-anomaly
```

检测静态标题卡，舍弃第一张标题卡之前的 OP/引子，默认重编码切割。输出 `941_split.json`、`941_1.mp4`、`941_2.mp4`；更多故事也可能生成 `_3` 等，但本入口只超分 `_1`、`_2`。

每集结尾**先用参考结尾图定位**：`assets/end1.jpg`、`assets/end2.jpg` 是每周都一样的故事交接卡（第 1 集后是「次回予告」卡，第 2 集后是 ED 的「原作」卡），在该集尾部（结尾前 210s）逐帧比对，相似度 ≥ 0.96 时**以命中帧为切点**（命中帧是卡片首帧，本身不进片段），日志打印 `参考图 end1.jpg 命中帧 00:11:49.042 相似度 0.9970`；未命中则打印窗口最高相似度（如 `00:21:28.754 相似度 0.9249`），便于判断差多少。

参考图**优先于黑帧**：黑场窗口里可能只是故事内部一次短促闪黑（942 因此把第 1 集结尾切早了 34s），所以参考图命中就用它。参考图缺失/未命中时才退回黑帧判据（下一张卡前 20–60s，或视频结束前 2–3 分钟）；两者都没有才保留原边界。`end3.jpg`… 依次对应第 3 集；可用 `--no-end-ref` 关闭参考图（只按黑帧）、`--end-ref-sim` 调阈值、`--end-ref-dir` 换目录。切割长度比 `end-start` 少 1 帧，避免边界那一帧（卡片首帧）被带进片尾。

标题卡不等于 2、某故事结尾未对齐黑帧/参考结尾图、或时长不在 8–13 分钟时，生成 `preview/contact_sheet.jpg` 等预览并询问。切分模式下预览总会刷新，END 行与真实切点一致：参考图定位的结尾显示**那张参考卡本身**并标注 `ref end1.jpg / sim 0.9970 @ 时间`，黑帧定位的显示黑场前最后一帧。

```text
按以上方案分割? [y/N]:
```

输入 `y` / `yes` 继续；其他输入取消切分，返回 2。启动器沿用既有规则，将这种人工取消视为正常退出，但明确显示 `Split cancelled`，不继续超分。资料分支仍收尾。其他分割错误返回失败。取消的剧集仍可在下次回车时恢复，再次进行确认。

重跑时，存在有效且非空的 split JSON，并且其中每段视频均达到 10 MiB，才跳过检测与切分。未全部切完时仍会复用已经完成的片段。直接运行分割脚本的 `--force` 可强制重做。

### 6.3 素材目录内的视频超分

超分可用 `UPSCALE_VIDEO` 开关（bat 顶部）关掉：设为 `0` 时调度器打印 `[SKIP] UPSCALE_VIDEO=0 - keeping the split clips`，视频主线在分割后结束，`pipeline_state.json` 记为 `split/completed`，只留 `941_1.mp4`、`941_2.mp4` 供检查结尾；改回 `1` 再跑一次（同一集会被复用，只补跑超分）即可。

调度器用专用 conda Python 直接调用 `scripts/upscale_tool.py`，显式传入本集的两个视频和输出目录。也可单独运行包装器：

```bat
bat\doraemon\run_upscale.bat ^
  "%USERPROFILE%\Desktop\doraemon_941\941_1.mp4" ^
  "%USERPROFILE%\Desktop\doraemon_941\941_2.mp4" ^
  --output-dir "%USERPROFILE%\Desktop\doraemon_941"
```

不复制输入，不扫描素材目录或 `C:\test\temps` 中的其他视频，也不删除历史 `C:\test` 内容。单独调用 `run_upscale.bat` 必须提供输入文件和 `--output-dir`；`--force` 重做已有输出。

每段视频按顺序执行：

1. 在 `<素材目录>/.upscale_tmp/<视频名>/` 创建独立 `in_frames` / `out_frames`。
2. CUDA ffmpeg 抽取 JPG 帧。
3. Real-ESRGAN `realesr-animevideov3` 配合自定义模型进行 2× 超分。
4. 以现有固定 30 FPS 重建，映射源视频第一音轨，使用 `h264_nvenc` 编码为 `<视频名>_4K.part.mp4`。
5. ffmpeg 成功后原子替换为 `<视频名>_4K.mp4`。
6. **本段完成或失败后立即删除本段临时帧和 `.part.mp4`，再处理下一段**；空的 `.upscale_tmp` 目录也删除，保留源视频和日志。

恢复粒度是**视频，不是帧**。目标非空 `_4K.mp4` 已存在时跳过；未完成的视频先删除同名临时帧和 `.part.mp4`，再从抽帧开始，不续接上次推理帧。不检查来源内容/时间，源片段变化后应显式用 `--force`。强制重做失败不会替换已有完整输出；空的编码输出不提升为最终视频。两个视频均完成时，调度器无需启动 GPU 超分进程。

任一视频失败仍尝试处理下一段，最终超分步骤返回非零。Ctrl+C 返回 130，停止当前子进程，清理当前视频临时文件，不继续下一段。日志为 `<素材目录>/run_log.txt`，每次启动追加时间戳记录。抽帧数量、推理帧数/速度/预计剩余时间、编码输出大小定期显示，整体阶段记录追加到 `pipeline_log.txt`。`--work-dir` 可指定临时根目录，只清理当前视频的工作目录，不清空其他文件。

## 7. 完成后的目录

典型双故事第 941 话，两张图和两个视频均成功放大：

```text
%USERPROFILE%\Desktop\doraemon_941\
├── metadata.json             # image_file 指向对应 *_4k.jpg
├── .metadata.lock
├── 941_1_4k.jpg               # 成功后已删除 941_1.jpg
├── 941_2_4k.jpg               # 成功后已删除 941_2.jpg
├── 941.mp4
├── 941_split.json
├── 941_1.mp4
├── 941_2.mp4
├── 941_1_4K.mp4
├── 941_2_4K.mp4
├── pipeline_state.json        # 本机阶段/状态恢复记录
├── pipeline_log.txt           # 调度与视频子进程日志，追加
├── materials_log.txt          # 资料子进程日志，追加
├── run_log.txt                # 逐视频超分日志，追加
└── preview\                  # 仅分割异常或旧运行遗留
```

正常结束没有 `.upscale_tmp` 或 `*_4K.part.mp4`。封面放大失败的故事保留原图；视频超分失败的故事没有新完成的 `_4K.mp4`，但源片段仍保留。

## 8. 失败与继续策略

| 情况 | 视频主线 / 最终结果 |
| --- | --- |
| Bootstrap、最新集解析、素材目录/状态写入失败 | 不启动分支，FAILED |
| 资料日志不可写、分支启动或抓取失败 | 视频继续；显示 WARNING，不改变主线退出码；抓取失败保留待恢复状态 |
| ComfyUI 不可用或单图放大失败 | 保留原图，其余流程继续 |
| TVer 下载失败 | 不分割/超分；等待资料分支后 FAILED |
| 分割异常且确认 y | 继续切分和超分 |
| 分割异常且取消 | 不超分；等待资料分支后正常退出，显示取消 |
| 分割其他错误、缺少 `_1`/`_2`、超分环境缺失 | 等待资料分支后 FAILED |
| 任一视频超分失败 | 清理本段临时文件，处理另一段，最终 FAILED |
| Ctrl+C / 关闭窗口 | 保留已完成视频；再次打开 bat 回车恢复本集，未完成视频重新抽帧 |
| 仅资料模式 / 指定集号抓取失败 | FAILED |

`Done` 表示视频主线正常退出或用户正常取消，不保证资料抓取、中文翻译或发布已完成；请同时检查资料警告。

## 9. macOS/Linux 差异与相关文件

`.sh` 默认 `DOWNLOAD_VIDEO=0`，仍先串行执行 `fetch_doraemon.py --latest`，再记录素材目录；设为 1 可下载和分割，但不执行 Windows Real-ESRGAN 超分。共享的 `fetch_doraemon.py` 已应用相同图片替换/删除逻辑。`.sh` 的分割错误提示行为保持原样。

| 文件 | 职责 |
| --- | --- |
| `bat/doraemon/01_download_latest_materials.bat` | Windows 提示、模式选择和结果展示 |
| `scripts/download_doraemon_materials.py` | 最新集解析、状态保存、并行调度和视频主线 |
| `scripts/fetch_doraemon.py` | 日文资料、图片下载、ComfyUI 放大、封面替换 |
| `scripts/download_tver.py` | TVer 检索和下载 |
| `scripts/split_episode.py` | 标题卡检测、异常确认、视频分割 |
| `scripts/upscale_tool.py` | 显式视频超分、素材目录输出、逐段清理 |
| `bat/doraemon/run_upscale.bat` | 专用超分环境包装器，转发参数 |
| `scripts/translate_doraemon.py` / `06_correct_subtitles` | 后续简介翻译、标签与字幕处理 |
