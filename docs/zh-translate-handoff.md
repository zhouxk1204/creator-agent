# 日译中字幕 + 烧录交接文档（Qwen3.5 9B 本地翻译）

> 日期：2026-09-30。状态：**代码已实现、单测全过（230 个），等真机装模型验证**。
> 链路：`视频 → ja-asr（日文 .srt）→ translate（本地 LLM 日译中 → .zh.srt，时间轴不变）→ --burn（ffmpeg 烧录 → .zh.mp4）`。
> 翻译 prompt 是用户定的"专业日中字幕翻译"10 条要求，原样写在 `translate/translator.py::SYSTEM_PROMPT`。

## 一、真机要做的事（模型安装）

代码不绑定部署方式——任何 **OpenAI 兼容接口**都行。推荐 Ollama（最省事）：

```bat
:: 1. 装 Ollama（https://ollama.com/download/windows），装完它常驻后台
:: 2. 拉模型（tag 以实际发布名为准，Qwen3.5 9B）
ollama pull qwen3.5:9b
:: 3. 确认服务在跑（Ollama 默认 localhost:11434）
curl http://localhost:11434/v1/models
:: 4. settings.yaml 的 translate.model 改成实际 tag（ollama list 里看到的名字）
```

不想用 Ollama 的话，llama.cpp `llama-server` / vLLM / LM Studio 都可以，把
`translate.base_url` 指过去即可（api_key 随便填）。

> RTX 5060 Ti 16G 跑 9B 量化版（Q4/Q5）没问题。翻译和 ASR 不共享环境、不共享显存
> （ASR 跑完才翻译），无需改 creator-asr-ja 环境。

## 二、验证步骤（按顺序）

1. **冒烟**（不烧录）：`uv run creator-agent translate <某个已有视频.mp4>`
   → 旁边出现 `<名>.zh.srt`，打开抽查：编号连续、时间轴和 `.srt` 完全一致、
   中文自然。控制台有 `[translate] [视频名] 批次 i/N：字幕 x~y 翻译中…` 进度。
2. **重跑**：`translate <视频> --force` 覆盖已有 .zh.srt。
3. **烧录**：`translate <视频> --burn`（已有 .zh.srt 会直接烧，不重新调模型）
   → `<名>.zh.mp4`，播放检查字幕位置/字号（字体默认微软雅黑 16，可调配置）。
4. **全链路**：`ja-asr <新视频> --translate --burn` 一条命令出烧录成片。

## 三、真机验证要点 / 高风险点

| # | 点 | 说明 | 排障 |
|---|---|---|---|
| 1 | 模型 tag | `translate.model` 必须和 `ollama list` 里的名字**完全一致** | 404/model not found → 改配置 |
| 2 | 思考模式 | 请求里带了 `chat_template_kwargs.enable_thinking=false`（Qwen3 系关闭思考，省时间）；服务端不认识这个字段会忽略，无副作用 | 输出若混入 `<think>` 内容，解析会自动忽略非编号行，但会拖慢——换关闭思考的方式 |
| 3 | 编号完整性 | 9B 模型偶尔漏编号/重复编号 → 自动重试 1 次 → 拆半递归 → 单条保底保留日文原文（日志 WARNING） | 大量"保留原文"说明模型/提示词不匹配，先手动 curl 一发看原始输出 |
| 4 | 烧录字体 | `subtitles` 滤镜需要 libass（full build ffmpeg 有）；中文字体用系统字体名，`Microsoft YaHei` Windows 自带 | 字幕变方块 → 字体名不对；`fc-list :lang=zh` 查可用名 |
| 5 | 批次大小 | 默认 30 条/批（要求 20~50）。9B 上下文有限，批太大容易漏编号，太小上下文断裂 | 漏编号多→调小到 20；语气不连贯→调大 `context_cues` |

## 四、这次做了什么（代码侧）

| 文件 | 角色 |
|---|---|
| `translate/translator.py`（新） | SYSTEM_PROMPT（10 条翻译要求）、SRT 分批（`batch_size`，带 `context_cues` 条上文参考）、编号解析校验、失败重试→拆半→保底、`process_translations` 编排。纯函数全部可单测 |
| `translate/burner.py`（新） | ffmpeg `subtitles` 滤镜烧录。字幕路径用**裸文件名 + cwd** 传，避开 Windows 盘符在滤镜语法里的转义坑 |
| `asr/subtitle.py` | 新增 `parse_srt`（解析回 TranscriptSegment，兼容 CRLF/多行/无序号行） |
| `config.py::TranslateSettings` + `settings.yaml` | `base_url/api_key/model/batch_size/context_cues/timeout_sec/ffmpeg_path/burn_font/burn_fontsize` |
| `cli/main.py` | 新命令 `translate`（独立翻译/烧录）；`ja-asr` 新增 `--translate` / `--burn` 链式执行 |
| `bat/translate.bat`（新） | 双击批量翻译 inbox，支持拖文件、`--burn` |
| `tests/unit/test_{translator,burner}.py`（新） | 18 个单测（分批/prompt/解析/重试/回退/时间轴保持/烧录命令构造） |

## 五、行为约定（要知道的）

- **时间轴/编号永不变**：翻译只替换文本，`.zh.srt` 的时间戳逐条复制自日文 `.srt`（有单测锁定）。
- **增量跳过**：已有 `.zh.srt` 的文件默认跳过（`--force` 重翻）；`--burn` 时若 `.zh.srt` 已存在则**不再调模型**直接烧。
- **ja-asr --translate 只翻译本轮新做的视频**：已跳过（有 .srt）的视频不会顺带翻译，要翻译用独立 `translate` 命令。
- **保底不中断**：单条翻译最终失败会保留日文原文并 WARNING，不会让整个文件失败（和 ASR 的 per-video 隔离原则一致）。
- **温度 0.2**：翻译求稳不求活。
- **说话人前缀**：`話者A:` 会作为文本一起被翻译（模型一般会翻成"说话人A："）。不想要的話先去掉前缀再翻，或在 prompt 里加规则。

## 六、排障速查

| 症状 | 看哪里 |
|---|---|
| 连接 refused | Ollama 没启动 / base_url 不对；`curl localhost:11434/v1/models` |
| model not found | `translate.model` 与 `ollama list` 不一致 |
| 控制台大量"保留原文" | 模型输出格式不符合「编号. 译文」；手动 curl 看原始输出 |
| .zh.srt 编号和 .srt 对不上 | 不可能（单测锁定）；除非是手工改过 .srt——以日文 .srt 为准 |
| 烧录字幕是方块/乱码 | 字体问题，见风险点 4 |
| 烧录极慢 | 正常（视频要重编码）；`-c:a copy` 音频不重编码 |
