# 最新话 Doraemon 素材下载完整流程

本文档说明 [`最新话doraemon素材下载.bat`](./最新话doraemon素材下载.bat) 当前实际执行的完整流程，包括运行模式、每一步的输入与输出、状态文件、外部依赖、失败行为，以及可供后续评估的优化点。

> 本文描述的是 **Windows `.bat` 主流程**。macOS/Linux 的 `.sh` 不是完全等价实现，差异见文末。

## 当前测试设置：视频阶段已关闭

目前 `.bat` 和 `.sh` 顶部的 `DOWNLOAD_VIDEO=0`，用于只测试资料抓取。双击 `.bat` 后直接回车，或运行同名 `.sh`，会：

- 抓取最新已播出话的日文资料、`metadata.json` 和封面（封面的 ComfyUI 放大仍按原逻辑执行）。
- 创建/使用桌面素材目录，并记录到 `logs/.last_doraemon_materials`。
- **跳过 TVer 视频下载、视频切分、复制到 `C:\test\temps` 和 Real-ESRGAN 视频放大**。
- 保留目录内已有的视频，不删除、不重新处理。

测试完成后，将实际使用的启动器顶部 `DOWNLOAD_VIDEO=0` 改为 `DOWNLOAD_VIDEO=1`，即可恢复下文所述完整流程。`.md` 是说明文件，实际运行入口为同名 `.bat` / `.sh`。

## 1. 这个启动器负责什么

完整模式的执行顺序：

```text
TV Asahi 最新已播出集信息
        ↓
抓取标题、简介、故事图片和封面
        ↓
按播出日期从 TVer 下载整集视频
        ↓
检测标题卡并切成每个故事的视频
        ↓
复制前两个故事视频到 C:\test\temps
        ↓
Real-ESRGAN 2× 超分，输出到 C:\test
```

它**不负责**：

- 标题/简介翻译和发布标签生成（移至 `06_correct_subtitles.bat` 前置执行）
- 日语 ASR
- 剪映字幕导出
- `*_origin.srt` 字幕矫正与重翻（由 `06_correct_subtitles.bat` 单独执行）
- 视频发布

## 2. 两种运行模式

启动器可以双击，也可以从命令行传入集号。

### 2.1 最新话完整模式

运行方式：

```bat
bat\doraemon\最新话doraemon素材下载.bat
```

出现提示后直接按回车：

```text
Episode number (e.g. 934, Enter = latest aired):
```

执行：

```text
抓取最新已播出集资料
→ TVer 下载
→ 自动分割
→ 复制前两个故事片段
→ Real-ESRGAN 超分
```

### 2.2 指定集号的资料模式

运行方式：

```bat
bat\doraemon\最新话doraemon素材下载.bat 941
```

也可以双击后输入 `941`。

只执行：

```text
抓取第 941 话的日文资料和封面
```

不会执行：

- TVer 下载
- 视频分割
- 复制到 `C:\test\temps`
- 视频超分

指定集号模式下仅执行抓取；抓取失败即返回失败。

## 3. 总输入

### 3.1 用户输入

| 输入 | 示例 | 作用 |
| --- | --- | --- |
| 集号为空 | 直接回车 | 选择最新已播出话并运行完整流程 |
| 明确集号 | `941` | 只抓取该话日文资料和封面 |

### 3.2 网络输入

| 来源 | 内容 | 用途 |
| --- | --- | --- |
| TV Asahi `doraemon/json/story.json` | 集号、播出日期列表 | 判断最新已播出话 |
| TV Asahi 故事页 | 标题、放送日、各故事标题、简介、版权、图片 URL | 生成 metadata、简介和封面 |
| TV Asahi 图片 URL | 每个故事的原始图片 | 下载 `{集号}_{故事序号}.jpg` |
| TVer API | 当前可播放的哆啦A梦剧集及播出日期 | 按 TV Asahi 放送日选择正确视频 |
| TVer HLS | 整集视频流 | 由 yt-dlp 下载，仅保存 MP4 |

默认 TVer 系列 ID：

```text
srtsxzl3si
```

### 3.3 本地输入与配置

| 输入 | 默认位置 | 用途 |
| --- | --- | --- |
| Python 虚拟环境 | 仓库 `.venv` | 执行全部 Python 脚本 |
| 项目设置 | `config/settings.yaml` | ffmpeg 等配置 |
| ComfyUI workflow | `C:\Users\34696\Documents\ComfyUI\user\default\workflows\doraemonn_cover.json` | 故事封面 2× 放大 |
| ComfyUI Core | `C:\agents\ComfyUI` | 无现有 ComfyUI 服务时尝试自动启动 |
| Real-ESRGAN | `C:\to4k\Real-ESRGAN` | 故事视频 2× 超分 |
| 超分 Python | `C:\Users\34696\miniconda3\envs\upscale\python.exe` | 运行 Real-ESRGAN |

## 4. 启动与环境准备

入口首先调用：

```bat
bat\_bootstrap.bat
```

其职责包括：

1. 定位仓库根目录
2. 切换到仓库目录
3. 检查 `.venv`
4. 缺少环境时通过 `uv sync` 安装依赖
5. 提供 `%PY%` Python 路径

Bootstrap 失败时流程立即退出。

之后激活：

```text
.venv\Scripts\activate.bat
```

## 5. 步骤一：确定集号并抓取 TV Asahi 资料

调用：

```bat
%PY% scripts\fetch_doraemon.py --latest
```

指定集号模式则调用：

```bat
%PY% scripts\fetch_doraemon.py 941
```

### 5.1 最新集选择规则

`--latest` 从 TV Asahi 的故事索引读取候选集，只保留：

```text
播出日期 ≤ 当前日本时间（JST）日期
```

然后选择播出日期最新的一集。TV Asahi 已经公布但尚未播出的下一集不会被选中。

### 5.2 输入

- 最新模式：TV Asahi 故事索引
- 指定模式：用户输入集号
- 对应故事页 HTML
- 各故事图片 URL
- 可选 ComfyUI 服务或本机 ComfyUI 安装

### 5.3 桌面输出

所有资料直接写入：

```text
%USERPROFILE%\Desktop\doraemon_<非补零集号>\
```

例如第 `0941` 话写入：

```text
%USERPROFILE%\Desktop\doraemon_941\
```

基础输出：

| 文件 | 内容 |
| --- | --- |
| `metadata.json` | 补零集号、故事页 URL、放送日、总标题、每个故事标题/简介/版权/图片 URL/本地图片名 |
| `941_1.jpg` | 第一个故事的 TV Asahi 原始图片 |
| `941_2.jpg` | 第二个故事的 TV Asahi 原始图片 |
| `941_1_jp.md` | 第一个故事的日文标题、封面嵌入和日文简介 |
| `941_2_jp.md` | 第二个故事的日文标题、封面嵌入和日文简介 |

如果 TV Asahi 页面包含不同扩展名，故事图片会保留该扩展名，不保证一定是 `.jpg`。

### 5.4 ComfyUI 封面输出

抓取脚本按以下顺序寻找 ComfyUI：

1. 环境变量 `COMFYUI_URL`
2. `http://127.0.0.1:8000`（ComfyUI Desktop）
3. `http://127.0.0.1:8188`
4. 都不可用时，尝试从 `C:\agents\ComfyUI` 自动启动服务

放大成功时增加：

```text
941_1_4k.jpg
941_2_4k.jpg
```

注意：文件名带 `_4k`，但当前 workflow 实际执行的是配置中的放大流程；文件名本身不保证精确 4K 分辨率。

ComfyUI 不可用或单图放大失败时：

- 不影响整个抓取流程
- 使用原始 `941_1.jpg` / `941_2.jpg` 作为封面
- 不生成对应 `_4k` 文件

### 5.5 状态输出

成功抓取后写入：

```text
仓库\logs\.last_fetched
仓库\logs\.last_fetched_date
```

示例：

```text
.last_fetched       → 941
.last_fetched_date  → 2026-11-07
```

完整模式依赖这两个文件继续执行。

完整模式创建素材目录后，还会记录本次实际使用的绝对路径：

```text
仓库\logs\.last_doraemon_materials → C:\Users\你的用户名\Desktop\doraemon_941
```

该路径在下载、切分前保存，后续步骤中断也保留；只查询指定集号简介的模式不会覆盖它。运行 `06_correct_subtitles.bat` 时会显示该目录，直接回车使用，也可以粘贴其他路径覆盖本次输入。macOS / Linux 入口写入同一状态文件，`06_correct_subtitles.sh` 不传路径时同样使用它。`logs/` 为本机运行状态，不随 Git 同步。

如果抓取脚本自己启动 ComfyUI，还可能写：

```text
仓库\logs\comfyui_server.log
```

### 5.6 失败行为

以下情况为致命失败：

- 集号非法
- 故事页 HTTP 失败
- 页面解析不到任何故事
- 关键文件写入失败

抓取失败后不会执行后续步骤。

## 6. 简介翻译移至字幕矫正前置步骤

素材下载入口不再启动本地翻译模型，也不生成中文简介或发布标签。

准备好剪映导出的 `*_origin.srt` 后，运行：

```bat
bat\doraemon\06_correct_subtitles.bat "%USERPROFILE%\Desktop\doraemon_941"
```

`06` 为每个待处理故事执行：

```text
读取同名 *_jp.md
→ 翻译标题/简介并生成发布标签，写入 *_zh.md
→ 日文字幕矫正
→ 中文字幕重翻
```

整次运行只启动一次模型；简介翻译与字幕步骤共用已加载的模型，中途不关闭服务。字幕提示词显式携带同一故事的日中标题/简介，不携带发布标签或全部聊天历史。

输出规则和失败策略见 [`06_correct_subtitles.md`](./06_correct_subtitles.md)。独立的 `02_fetch_doraemon.bat` 和每周定时任务仍保留原有简介翻译功能。

## 7. 步骤二：按播出日期从 TVer 下载整集

仅最新话完整模式执行。

调用：

```bat
%PY% scripts\download_tver.py ^
  --out-dir "%USERPROFILE%\Desktop\doraemon_941" ^
  --name 941 ^
  --broadcast-date 2026-11-07
```

### 7.1 输入

- TVer 固定系列 `srtsxzl3si`
- TVer 当前可播放节目列表
- `.last_fetched_date` 中的 TV Asahi 放送日
- 可选系统代理，例如 `HTTPS_PROXY`
- yt-dlp
- pycryptodomex（让 yt-dlp 在进程内解密并并行下载 HLS）
- ffmpeg（优先复用 `config/settings.yaml` 的 `asr.ffmpeg_path`）

### 7.2 日期匹配

例如：

```text
2026-11-07 → 在 broadcastDateLabel 中匹配“11月7日”
```

当前只比较月和日，不比较年份。

此保护用于避免 TVer 更新滞后时，把上一话错误保存为新集号。

### 7.3 输出

仅写入桌面素材目录中的整集视频：

```text
941.mp4
```

HLS 下载后 remux 为 MP4，不主动重编码；不保存 TVer 缩略图或 info-json。TV Asahi 的 `metadata.json` 和各故事图片仍由抓取步骤生成。

下载过程中可能暂时出现 `.part` 等 yt-dlp 临时文件，成功完成后通常会清理。以前生成的 `941.jpg`、`941.info.json` 不会自动删除。

### 7.4 下载参数

- 8 个并行 HLS fragments
- 元数据重试 10 次
- fragment 重试 10 次
- MP4 remux
- 禁止保存缩略图和 info-json

### 7.5 失败行为

以下情况为致命失败：

- TVer API 请求失败
- 找不到与播出日期匹配的可播放节目
- yt-dlp 下载失败
- 网络或日本地区访问条件不满足

流程在此停止，不执行分割和超分。

## 8. 步骤三：检测标题卡并分割故事视频

调用：

```bat
%PY% scripts\split_episode.py ^
  "%USERPROFILE%\Desktop\doraemon_941\941.mp4" ^
  -o "%USERPROFILE%\Desktop\doraemon_941" ^
  --confirm-anomaly
```

### 8.1 输入

- `941.mp4`
- OpenCV / NumPy
- ffmpeg / ffprobe
- 标题卡检测和黑帧吸附规则

### 8.2 当前自动切割规则

1. 扫描静态标题卡
2. 默认每个标题卡开始一个故事
3. 默认舍弃第一个标题卡之前的 OP/引子
4. 每个故事末尾尝试吸附到黑帧
5. 根据检测结果生成故事区间
6. 默认重编码切割；没有传 `--copy`

### 8.3 正常输出

至少写入：

```text
941_split.json
941_1.mp4
941_2.mp4
```

如果检测到更多故事，也可能输出：

```text
941_3.mp4
...
```

`941_split.json` 包括：

- 源视频名
- FPS
- 总时长
- 是否舍弃片头
- 各故事名称、开始时间、结束时间
- 每个标题卡起止时间、相似度、切入/切出类型、是否检测到标题文字

### 8.4 异常定义

使用 `--confirm-anomaly` 时，以下任一情况会要求人工确认：

- 标题卡数量不是 2
- 某故事结尾未对齐黑帧
- 某故事时长不在 8–13 分钟

### 8.5 异常预览输出

检测到异常时写入：

```text
preview\
├── contact_sheet.jpg
├── candidate_01.jpg
├── candidate_02.jpg
├── end_01.jpg
├── end_02.jpg
└── ...
```

程序会打开 `contact_sheet.jpg` 并询问：

```text
按以上方案分割? [y/N]:
```

- 输入 `y` / `yes`：按建议区间切割
- 其他输入或直接回车：取消切割，返回退出码 2

主启动器把退出码 2 视为“用户正常取消”：

- 停止后续超分
- 保留预览图和 `941_split.json`
- 最终整体退出码改为 0，并显示 `Done`

其他分割错误为致命失败。

## 9. 步骤四：复制前两个故事片段到超分暂存目录

分割成功后，主启动器明确检查：

```text
Desktop\doraemon_941\941_1.mp4
Desktop\doraemon_941\941_2.mp4
```

任一缺失都会失败。

然后复制并覆盖：

```text
C:\test\temps\941_1.mp4
C:\test\temps\941_2.mp4
```

注意当前行为：

- 只复制 `_1` 和 `_2`
- 不复制可能存在的 `_3` 及以后故事
- 不会先清空 `C:\test\temps`
- `temps` 中以前留下的其他视频继续存在

复制失败为致命错误。

## 10. 步骤五：执行视频超分

素材流程不启动翻译模型，也不关闭用户已经启动的模型服务。

调用：

```bat
bat\doraemon\run_upscale.bat
```

其使用专用环境执行：

```text
C:\Users\34696\miniconda3\envs\upscale\python.exe
scripts\upscale_tool.py
```

### 10.1 超分输入

`upscale_tool.py` 会扫描 `C:\test\temps` 中的**所有**以下格式视频：

```text
.mp4  .mkv  .mov  .avi
```

它不是只处理本次复制的 `941_1.mp4` 和 `941_2.mp4`。

### 10.2 外部依赖

```text
Real-ESRGAN 目录：C:\to4k\Real-ESRGAN
推理脚本：       C:\to4k\Real-ESRGAN\inference_realesrgan.py
模型权重：       C:\to4k\Real-ESRGAN\weights\2x_AniScale2S_Compact_i8_60K.pth
ffmpeg：          C:\to4k\Real-ESRGAN\ffmpeg.exe
Python：          C:\Users\34696\miniconda3\envs\upscale\python.exe
```

### 10.3 处理过程

每个视频依次执行：

1. 清空并重建 `C:\test\in_frames`、`C:\test\out_frames`
2. 使用 CUDA ffmpeg 抽取 JPG 帧
3. 使用 Real-ESRGAN `realesr-animevideov3` 和自定义模型进行 2× 超分
4. 以固定 30 FPS 读取放大帧
5. 从原视频映射第一条音轨
6. 使用 `h264_nvenc` 编码视频
7. 输出 MP4
8. 再次清空临时帧目录

### 10.4 输出

每个输入视频输出到 `C:\test` 根目录：

```text
C:\test\941_1_4K.mp4
C:\test\941_2_4K.mp4
```

`temps` 中如果还有其他视频，也会产生对应：

```text
C:\test\<其他 stem>_4K.mp4
```

日志：

```text
C:\test\run_log.txt
```

每次启动超分时会重写该日志文件。

如果目标 `_4K.mp4` 已经存在，该视频会直接跳过，即使来源视频后来发生变化也不会自动重新生成。

任一视频失败会继续处理其他视频，但最终整个超分步骤返回失败；主启动器显示 `FAILED`。

## 11. 完整成功后的输出目录树

典型双故事剧集 `941` 完整成功后：

### 11.1 桌面主素材目录

```text
%USERPROFILE%\Desktop\doraemon_941\
├── metadata.json
├── 941_1.jpg
├── 941_2.jpg
├── 941_1_4k.jpg              # ComfyUI 成功时存在
├── 941_2_4k.jpg              # ComfyUI 成功时存在
├── 941_1_jp.md
├── 941_2_jp.md
├── 941.mp4
├── 941_split.json
├── 941_1.mp4
├── 941_2.mp4
└── preview\                  # 仅异常确认或旧运行遗留时可能存在
    ├── contact_sheet.jpg
    ├── candidate_*.jpg
    └── end_*.jpg
```

### 11.2 超分暂存与最终输出

```text
C:\test\
├── temps\
│   ├── 941_1.mp4
│   └── 941_2.mp4
├── 941_1_4K.mp4
├── 941_2_4K.mp4
├── run_log.txt
├── in_frames\                # 处理结束后为空
└── out_frames\               # 处理结束后为空
```

### 11.3 仓库运行状态

```text
仓库\logs\
├── .last_fetched
├── .last_fetched_date
└── comfyui_server.log        # 仅脚本自动启动 ComfyUI 时可能存在
```

## 12. 输入/输出速查表

| 步骤 | 主要输入 | 主要输出 | 输出位置 |
| --- | --- | --- | --- |
| 抓取资料 | 集号、TV Asahi 页面和图片 | metadata、故事图、日文简介、可选放大封面 | 桌面 |
| 后续 `06` 简介前置 | 同故事 `_jp.md`、本地 LLM | `_zh.md` 中文简介和发布标签 | 同一素材目录，不属于下载流程 |
| TVer 下载 | 放送日、TVer API/HLS | 仅整集 MP4 | 桌面 |
| 故事分割 | 整集 MP4 | split JSON、故事 MP4、可选预览图 | 桌面 |
| 超分暂存 | 前两个故事 MP4 | 覆盖复制的故事 MP4 | `C:\test\temps` |
| Real-ESRGAN | `temps` 中所有视频 | 2× 超分 MP4、日志 | `C:\test` |

## 13. 失败与继续策略速查

| 失败/情况 | 是否继续 | 最终状态 |
| --- | --- | --- |
| Bootstrap 失败 | 否 | FAILED |
| TV Asahi 抓取失败 | 否 | FAILED |
| `.last_fetched` 缺失 | 否 | FAILED |
| TVer 日期无匹配 | 否 | FAILED |
| TVer 下载失败 | 否 | FAILED |
| 分割异常，用户确认 y | 是 | 继续切割和超分 |
| 分割异常，用户取消 | 不继续超分 | 被视为正常 Done |
| 分割脚本其他错误 | 否 | FAILED |
| `_1.mp4` 或 `_2.mp4` 缺失 | 否 | FAILED |
| 复制到 `C:\test\temps` 失败 | 否 | FAILED |
| 任一超分视频失败 | 处理其余后失败 | FAILED |

## 14. macOS/Linux `.sh` 与 Windows `.bat` 的差异

[`最新话doraemon素材下载.sh`](./最新话doraemon素材下载.sh) 只近似覆盖前半流程，并不等价：

| 能力 | Windows `.bat` | macOS/Linux `.sh` |
| --- | --- | --- |
| Bootstrap / 自动建 venv | 有 | 无，直接使用 `uv run` |
| 交互输入集号 | 有 | 无，只读命令行参数 |
| 自动启动/关闭翻译服务 | 不执行，移至 `06` | 不执行，移至 `06` |
| TV Asahi 抓取 | 有 | 有 |
| 日文简介输出 | 桌面 `_jp.md` | 桌面 `_jp.md` |
| 简介翻译 | 移至 `06` 前置 | 移至 `06` 前置 |
| TVer 下载 | 有 | 有 |
| 视频分割 | 有 | 有 |
| 复制到 `C:\test\temps` | 有 | 无 |
| Real-ESRGAN 超分 | 有 | 无 |
| 自动打开素材目录 | Explorer | `open` |

`.sh` 中分割退出码不是 2 时，即使分割失败，脚本仍会打印 `Done! Materials: ...`，但最终会以实际非零退出码结束。

## 15. 当前可评估的优化点

以下是根据现有实现整理的观察项，是否调整由实际使用需求决定。

### 15.1 简介素材已统一到桌面

- `fetch_doraemon.py` 在桌面生成 `941_1_jp.md` 等日文简介
- 后续 `06` 调用 `translate_doraemon.py` 的单故事翻译函数，在同目录写入 `941_1_zh.md`，再衔接字幕处理
- 封面使用同目录图片的 Markdown 相对链接，不再依赖或写入 Obsidian Vault
- Windows 和 macOS/Linux 使用相同的简介输入输出规则；已有 Vault 文件不会自动删除

### 15.2 `C:\test\temps` 不清理

现状：主启动器只覆盖复制本集 `_1`、`_2`，但保留其他旧视频；超分工具会处理目录中所有视频。

风险：

- 意外重新扫描旧视频
- 旧视频没有 `_4K` 输出时会被额外处理
- 难以确认本次究竟处理了哪些输入

可评估：运行前清理目录，或让超分工具接收明确的两个输入路径。

### 15.3 主流程固定只处理两个故事

现状：分割器支持任意数量的故事，但主启动器只检查和复制 `_1.mp4`、`_2.mp4`。

可评估：从 `*_split.json` 动态读取片段列表，或显式限定“必须正好两个故事”。

### 15.4 视频超分输出仍与桌面素材分离

当前素材输出分布：

1. 桌面 `doraemon_<集号>`：抓取资料、整集及分割视频；后续 `06` 另写中文简介和字幕
2. `C:\test`：超分暂存与最终视频

可评估：超分视频是否也应写回桌面集目录，使 `C:\test` 只作为临时缓存。

### 15.5 硬编码路径较多

包括：

- ComfyUI workflow/Core/venv
- Real-ESRGAN、模型、ffmpeg
- 超分 conda Python
- `C:\test`

可评估：集中到 `config/settings.yaml` 或环境变量，减少换机器时修改源码。

### 15.6 超分固定 30 FPS 和第一音轨

现状：重建视频时固定 `-r 30`，音频固定映射 `1:a:0`。

可评估：读取并保留源视频 FPS；无音轨或多音轨时给出更明确策略。

### 15.7 已存在 `_4K.mp4` 无条件跳过

现状：只看目标是否存在，不比较源视频时间或内容。

可评估：增加 `--force`、mtime/hash 判断，避免源片段更新后仍沿用旧超分结果。

### 15.8 分割取消被显示为成功

现状：用户在异常确认时选择否，主启动器把退出码 2 改成 0，最终显示 `Done`。

可评估：单独显示 `CANCELLED / NEEDS REVIEW`，避免与完整成功混淆。

### 15.9 简介翻译已与素材下载解耦

素材下载的 `Done` 只表示该入口执行完成，不包括中文简介或发布标签。

这些产物由后续 `06` 前置生成，和字幕处理共用同一个模型服务；各阶段失败策略见 `06_correct_subtitles.md`。

### 15.10 播出日期状态可能陈旧

现状：抓取成功但页面日期解析失败时，不会主动删除旧 `.last_fetched_date`。

风险：后续可能使用上一次运行遗留的播出日期匹配 TVer。

可评估：每次抓取前清理日期状态，或把集号和日期放在同一份原子状态 JSON 中。

### 15.11 TVer 日期只比较月日

现状：`2026-11-07` 转为匹配 `11月7日`，不检查年份。

可评估：结合 TVer 元数据中的年份字段或发布时间做更严格验证。

### 15.12 TVer 无任何可播放节目时错误出现较晚

现状：`download_tver.py` 在列表为空时直接返回成功；主流程会到分割阶段才因缺少 `<ep>.mp4` 失败。

可评估：管线模式下无可播放节目应立即返回非零，提供更准确错误。

### 15.13 预览目录可能是旧运行遗留

现状：正常无异常运行不会主动清理以前的 `preview\`。

可评估：在每次分割开始时清理旧预览，或给预览目录加时间戳。

## 16. 相关文件

| 文件 | 职责 |
| --- | --- |
| `bat/doraemon/最新话doraemon素材下载.bat` | Windows 主控流程 |
| `bat/doraemon/最新话doraemon素材下载.sh` | macOS/Linux 简化流程 |
| `scripts/fetch_doraemon.py` | TV Asahi 资料、封面、简介抓取 |
| `scripts/translate_doraemon.py` | 日文标题/简介翻译与标签生成 |
| `scripts/download_tver.py` | TVer 检索和下载 |
| `scripts/split_episode.py` | 标题卡检测、黑帧吸附、视频分割 |
| `bat/doraemon/run_upscale.bat` | 调用专用超分 Python 环境 |
| `scripts/upscale_tool.py` | 抽帧、Real-ESRGAN、重新编码 |
| `bat/doraemon/06_correct_subtitles.bat` | 独立字幕矫正和重翻入口 |
| `bat/doraemon/06_correct_subtitles.md` | 字幕矫正与翻译规范 |
