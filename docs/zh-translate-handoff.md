# 日译中字幕 + 烧录 + 二次学习交接文档（Qwen3.5 9B 本地翻译）

> 日期：2026-09-30。状态：**代码已实现、单测全过（255 个），等真机装模型验证**。
> 链路：`视频 → ja-asr（日文 .srt）→ translate（本地 LLM 日译中 → .zh.srt，时间轴不变）→ --burn（ffmpeg 烧录 → .zh.mp4）`。
> 翻译 prompt 是用户定的"专业日中字幕翻译"10 条要求，原样写在 `translate/translator.py::SYSTEM_PROMPT`。
> 另有**二次学习**回路：人工修正字幕 → `learn` 差异分析 → 知识库 → 下次翻译自动注入 prompt。

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
| `translate/alignment.py`（新） | 02 vs 03 按时间轴对齐成块：same / time_changed / split / merge |
| `translate/memory.py`（新） | `MemoryStore`：knowledge/ 读写、去重合并、`prompt_section` 检索注入 |
| `translate/learn.py`（新） | `analyze_episode`（对齐 + LLM 分类 + 04_analysis.json）、`learn_project`、`write_reports` |
| `cli/main.py` + `bat/learn.bat` | 新命令 `learn` |
| `tests/unit/test_{alignment,memory,learn}.py`（新） | 对齐/去重/注入/端到端幂等 |

## 五、行为约定（要知道的）

- **时间轴/编号永不变**：翻译只替换文本，`.zh.srt` 的时间戳逐条复制自日文 `.srt`（有单测锁定）。
- **增量跳过**：已有 `.zh.srt` 的文件默认跳过（`--force` 重翻）；`--burn` 时若 `.zh.srt` 已存在则**不再调模型**直接烧。
- **ja-asr --translate 只翻译本轮新做的视频**：已跳过（有 .srt）的视频不会顺带翻译，要翻译用独立 `translate` 命令。
- **保底不中断**：单条翻译最终失败会保留日文原文并 WARNING，不会让整个文件失败（和 ASR 的 per-video 隔离原则一致）。
- **温度 0.2**：翻译求稳不求活。
- **说话人前缀**：`話者A:` 会作为文本一起被翻译（模型一般会翻成"说话人A："）。不想要的話先去掉前缀再翻，或在 prompt 里加规则。

## 六、二次学习（learn）

把人工修正后的字幕喂回来，让 Qwen3.5 9B 当"审校专家"分析 AI 翻译和人工译文的差异，
沉淀成知识库；之后每次 `translate` / `ja-asr --translate` 会**自动把相关知识注入翻译 prompt**。

> 环境要求：**和翻译共用同一个 Ollama / Qwen3.5 9B 服务**（第一节装好即可，无新增依赖）。
> learn 只是多调几次同一个接口。

### 目录约定（settings.yaml 的 `translate.project_dir`，默认 `./subtitle-project`）

```
subtitle-project/
├── episodes/<剧集名>/          ← 每集一个目录，放 3 个输入文件
│   ├── 01_original_ja.srt      Qwen3-ASR 原始日文（ja-asr 产物）
│   ├── 02_ai_zh.srt            AI 初翻（translate 产物的 .zh.srt 改名）
│   ├── 03_final_zh.srt         人工修改后的最终字幕
│   └── 04_analysis.json        （learn 自动写出：对齐 + 差异明细）
├── knowledge/                  （learn 自动维护，translation_rules.md 可手工编辑）
│   ├── error_cases.json        错误案例库（带类别标签）
│   ├── terminology.json        术语/人名固定译法
│   ├── segmentation_rules.json 结构/时间轴修改记录
│   ├── translation_rules.md    可泛化的翻译规则（每条一行 "- " 开头）
│   └── translation_memory.json 编译产物（translator 实际读取的）
└── reports/
    ├── episode_report.csv      每条差异一行（Excel 直接开）
    └── project_report.html     全项目汇总（类别分布、术语、规则、案例）
```

### 使用

```bat
bat\learn.bat                :: 扫描 project_dir/episodes/ 下所有剧集
uv run creator-agent learn   :: 等价命令行（--model/--base-url 可覆盖配置）
```

流程：02 和 03 按时间轴对齐（same/时间轴修改/拆分/合并，容差 0.05s）→ 有差异的块按 20 条/批
发给 LLM 分类（用户的类别体系：翻译错误 9 类 / 字幕结构 6 类 / 时间轴 4 类，同时提取术语和
可泛化规则）→ 写 04_analysis.json → 去重合并进 knowledge/ → 生成 reports/。

### 注入逻辑（translate 时自动发生，无需干预）

每个翻译批次的 prompt 里会插入（`memory.py::MemoryStore.prompt_section`）：

1. **【术语库】** 本批日文里字面上出现的术语 → 强制统一译名；
2. **【历史错误案例】** 与本批最相似（字符 bigram 重合度）的 top-K 条（`memory_cases`，默认 10）；
3. **【翻译规则】** 全部规则的前 15 条。

知识库不存在时不注入，行为和以前完全一样。

### 注意

- **幂等**：案例按 (日文, AI译文) 去重、术语按日文去重（人工修正覆盖旧值）、规则按文本去重
  → 同一集重复 learn 不会膨胀。新增剧集直接丢进 episodes/ 再跑一次即可。
- **LLM 失败的批次不会中断**：该批只记录结构差异（拆分/合并/时间轴），文字差异分类留空。
- `translation_rules.md` 是给人看的，可以手工增删行，下次翻译即生效（learn 重跑不会覆盖手工行——追加式去重合并）。

## 七、排障速查

| 症状 | 看哪里 |
|---|---|
| 连接 refused | Ollama 没启动 / base_url 不对；`curl localhost:11434/v1/models` |
| model not found | `translate.model` 与 `ollama list` 不一致 |
| 控制台大量"保留原文" | 模型输出格式不符合「编号. 译文」；手动 curl 看原始输出 |
| .zh.srt 编号和 .srt 对不上 | 不可能（单测锁定）；除非是手工改过 .srt——以日文 .srt 为准 |
| 烧录字幕是方块/乱码 | 字体问题，见风险点 4 |
| 烧录极慢 | 正常（视频要重编码）；`-c:a copy` 音频不重编码 |
