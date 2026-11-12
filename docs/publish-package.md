# Doraemon metadata.json 素材与发布标准

`metadata.json` 是剧集资料、中文翻译、字幕背景和发布配置的唯一来源。`fetch_doraemon.py`、`translate_doraemon.py`、`06_correct_subtitles` 和 `publish_dual.py` 共用它；不再生成或读取日中简介 Markdown，也不再读取旧的 `title.txt` / `desc.txt` / `tags.txt` / `meta.txt` 发布包。

旧文件不会自动删除，但不能再作为输入。已有无 `schema_version` 的剧集 metadata 可在更新时升级到 v2。

## 1. 目录与故事标识

```text
Desktop/doraemon_941/
├── metadata.json
├── 941_1.jpg                     # TV Asahi 原始故事图，B站直接使用
├── 941_2.jpg
├── 941_1_origin.srt              # 对应 story_id = 941_1
├── 941_1_jp_fixed.srt
├── 941_1_zh_fixed.srt
├── 941_1_final.mp4               # 人工确认的最终发布视频
├── 941_1_douyin_portrait.jpg     # 单独制作，3:4
└── 941_1_douyin_landscape.jpg    # 单独制作，4:3
```

一个 metadata 可以有多个故事，也可以在同目录放整集、切分、超分及成片。发布器**不扫描或猜测视频文件**，每个故事必须明确填写 `publish.video_path`。

- 根级 `episode` 使用补零字符串，如 `"0941"`。
- `story_index` 是正整数；`story_id` 使用不补零集号和序号，如 `"941_1"`。
- 字幕格式为 `<集号>_<故事序号>_origin.srt`；`06` 按故事标识匹配，而不是按 JSON 数组顺序猜测。
- 相对路径以 **metadata.json 所在目录**为基准，绝对路径也可使用。
- Windows JSON 路径推荐写 `C:/test/941_1_final.mp4`；反斜杠必须转义为 `C:\\test\\941_1_final.mp4`。

## 2. Schema v2

下面是抓取后、尚未翻译及指定成片的单故事示例。实际文件通常有两个故事：

```json
{
  "schema_version": 2,
  "episode": "0941",
  "broadcast_date": "2026-11-07",
  "episode_title": "日文整集标题",
  "url": "https://www.tv-asahi.co.jp/doraemon/story/0941/",
  "stories": [
    {
      "story_id": "941_1",
      "story_index": 1,
      "title": "日文标题",
      "synopsis": "日文简介第一段。\n日文简介第二段。",
      "copyright": "",
      "image_file": "941_1.jpg",
      "zh": null,
      "publish": {
        "title": null,
        "video_path": null,
        "douyin": {
          "tags": [],
          "cover_portrait_path": null,
          "cover_landscape_path": null
        },
        "bilibili": {
          "tags": [],
          "tid": 24
        }
      }
    }
  ]
}
```

`06` 或独立简介翻译成功后，更新同一故事的：

- `zh.title`：中文故事标题，不带整集发布前缀。
- `zh.synopsis`：完整中文简介，保留段落和换行；JSON 用 `\n` 表示换行。
- `zh.source_hash`：程序根据日文 `title` / `synopsis` 计算的 `sha256:...`，不要手填或伪造。
- `zh.translated_at`：程序写入的 UTC 翻译时间。
- `publish.title`：初次自动生成为 `哆啦a梦941 中文故事标题`，可人工编辑。
- `publish.douyin.tags` / `publish.bilibili.tags`：平台各自的标签数组，不带 `#`。

人工编辑中文措辞时无需改 `source_hash`。日文源字段发生变化后旧中文会被判定为过期，必须重新翻译后才能用于字幕/发布。重复抓取会保留已有中文和人工发布配置，但不会把旧译文冒充为新源的译文。metadata 更新使用文件锁和原子替换，避免多故事更新互相覆盖。

## 3. 发布字段

| 字段 | 规则 |
| --- | --- |
| `publish.title` | 两平台共用，格式 `哆啦a梦941 XXXXX`；单行；抖音 ≤55 字，单独发B站 ≤80 字 |
| `publish.video_path` | 必填，明确指向该故事最终成片；支持 `.mp4 .mov .flv .mkv .avi`，建议 H.264 + AAC MP4 |
| `publish.douyin.tags` | 1–5 个字符串，不带 `#`；抖音描述由它生成，如 `#哆啦A梦 #大雄 #新番` |
| `publish.douyin.cover_portrait_path` | 必填，独立制作的 **3:4** 竖封面，建议 1080×1440 |
| `publish.douyin.cover_landscape_path` | 必填，独立制作的 **4:3** 横封面，建议 1440×1080；不可与竖图指向同一文件 |
| `publish.bilibili.tags` | 1–11 个字符串，不带 `#` |
| `publish.bilibili.tid` | 正整数分区号，默认 24；以当前B站投稿页为准，可用 `--tid` 覆盖 |
| `publish.schedule` | 可选，`"YYYY-MM-DD HH:MM"`；也可用 `--schedule` 覆盖 |

每个标签最多 20 字，不得含空白、逗号或 `#`，同平台不得重复。生成时最精准的标签优先放前面，供抖音前 5 个使用。

**B站不再单独存简介和封面配置**：

- 描述直接读取所选故事的 **`zh.synopsis`**，保留内部换行，不重复写 `publish.bilibili.synopsis`。超过 2000 字时发布前报错，不静默截断。
- 封面直接读取所选故事的 **`image_file`**，不重复写 `publish.bilibili.cover_path`。
- 所有封面支持 `.jpg .jpeg .png`。抖音没有桌面公共封面、通用封面或原始故事图回退；想复用某张制作好的封面时，显式填写其路径。

只发一个平台时，只校验该平台要求的标签和封面；两个平台都发时，全部校验通过后才开始上传。

抖音固定自主声明 **「虚构演绎，仅供娱乐」**；无法设置时停止发布。固定合集 **「哆啦A梦2026合集」**，需预先存在于创作者账号；未找到时上传器会警告并保持未归集。B站不使用这两个选项。

## 4. 日常操作

1. 运行 `02_fetch_doraemon`（资料及独立简介翻译），或最新话素材下载入口（仅抓取，中文延后至 `06`）。
2. 将 `941_1_origin.srt` 等剪映双语字幕放进素材目录，运行 `06_correct_subtitles`。它先写中文 metadata，再从 metadata 读取匹配的日中背景进行字幕处理。
3. 剪辑并导出最终视频，单独制作抖音两张封面，在该故事的 `publish` 内填写三个文件路径，检查发布标题与两个平台的标签。
4. 先运行校验，再确认发布：

```bat
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --dry-run
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941\metadata.json" --story 941_1
bat\publish_dual.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --only bilibili
bat\publish_douyin.bat "%USERPROFILE%\Desktop\doraemon_941" --story 941_1 --dry-run
```

不传路径时使用 `logs/.last_doraemon_materials` 保存的本机目录；双击 BAT 可回车接受默认，或粘贴其他目录/metadata.json。多个故事时必须选择 `story_id`；命令行和自动化任务应显式传 `--story`。

`--dry-run` 只校验素材并打印命令，不上传。正式发布显示摘要，输入 `Y` 才继续；只有明确指定 `--yes` 才跳过确认。账号通过 `--douyin-account` / `--bilibili-account` 选择，默认均为 `default`。某个平台失败仍继续另一个，最后返回非零，可用 `--only` 单独重试失败平台，避免重复发布已成功的平台。

`06` 的 `--force` 只允许覆盖字幕；不代表重新翻译已有简介。需要重新翻译中文 metadata 时另加 `--refresh-translation`；已有字幕也要覆盖时两者一起用。刷新简介会清空并重新生成两平台标签（包括人工修改的标签），但保留人工发布标题和视频/封面路径。普通重跑复用当前有效的中文和标签，只补齐空缺或不合法的平台标签。成功的中文 metadata 在后续标签或字幕失败时仍保留。

安装、扫码登录和上传故障处理见 [`bat/publish_dual.md`](../bat/publish_dual.md)。
