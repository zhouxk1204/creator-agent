"""Correct JianYing bilingual Japanese subtitles and retranslate them to Chinese."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creator_agent.config import TranslateSettings
from creator_agent.translate.llm import chat
from creator_agent.translate.memory import MemoryStore
from creator_agent.translate.vault import parse_synopsis_md

_SRT_TIME = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
)
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]")

CORRECTION_SYSTEM_PROMPT = """你是《哆啦A梦》日语字幕审校员。输入来自剪映日语 ASR，没有音频可听。

任务：纠正高置信度 ASR 误识，并按完整日语句子重新组合字幕。
规则：
1. 忠实保留原始措辞、语序和口语；不得根据剧情简介脑补原字幕没有的台词。
2. 简介只用于确认人物、地点、道具、剧情背景；与 ASR 冲突时以 ASR 为准。
3. 只修正上下文能唯一确定的音近误识、人物名和专名；有多个合理候选时保留原文并记录疑问。
4. 可合并相邻原始条目；不要跨说话人、场景或独立句。语气词按语义决定是否合并。
5. source_ids 必须连续，且每个输入 ID 恰好使用一次；不得创造或丢失 ID。
6. 不输出时间轴。只输出 JSON，不要 Markdown 代码块或解释。

输出格式：
{"groups":[{"source_ids":[1,2],"text":"修正后的完整日语"}],
 "questions":[{"source_ids":[3],"reason":"确实无法唯一判断的原因"}]}
没有真正疑问时 questions 必须是空数组。"""

TRANSLATION_SYSTEM_PROMPT = """你是《哆啦A梦》专业日中字幕翻译。

任务：把已审校的日文字幕逐条翻译为自然、简洁的中文字幕。
规则：
1. 忠实逐条翻译，不增添、遗漏或解释原文信息；保留人物语气和情绪。
2. 人名统一：ドラえもん→哆啦A梦、のび太→大雄、しずか→静香、ジャイアン→胖虎、スネ夫→小夫。
3. 人物、地点、道具和固定梗在整集中保持一致；简介只作为背景，不得覆盖字幕。
4. 每个输入 ID 恰好输出一次，不能合并或拆分；译文不得残留日文假名。
5. 只输出 JSON，不要 Markdown 代码块或解释。

输出格式：
{"translations":[{"id":1,"text":"中文译文"}],
 "questions":[{"ids":[2],"reason":"确实影响含义且无法唯一判断的原因"}]}
没有真正疑问时 questions 必须是空数组。"""


class SubtitleCorrectionError(RuntimeError):
    """The input or an LLM response violates the correction contract."""


@dataclass(frozen=True)
class SourceCue:
    source_id: int
    start: str
    end: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class FixedCue:
    source_ids: tuple[int, ...]
    start: str
    end: str
    text: str


@dataclass(frozen=True)
class ReviewQuestion:
    stage: str
    ids: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class CorrectionResult:
    source: Path
    jp_fixed: Path
    zh_fixed: Path
    questions: Path | None
    source_cues: int
    fixed_cues: int


def _timestamp_ms(value: str) -> int:
    hours, minutes, rest = value.replace(".", ",").split(":")
    seconds, milliseconds = rest.split(",")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(milliseconds)


def parse_strict_srt(path: Path) -> list[SourceCue]:
    """Parse every SRT block or fail; never silently discard malformed cues."""
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    if not raw:
        raise SubtitleCorrectionError(f"{path.name}: 字幕为空")
    cues: list[SourceCue] = []
    for position, block in enumerate(re.split(r"\n\s*\n", raw), 1):
        lines = block.splitlines()
        if len(lines) < 3:
            raise SubtitleCorrectionError(f"{path.name}: 第 {position} 个字幕块不完整")
        try:
            index = int(lines[0].strip())
        except ValueError as exc:
            raise SubtitleCorrectionError(f"{path.name}: 第 {position} 个字幕块编号无效") from exc
        if index != position:
            raise SubtitleCorrectionError(f"{path.name}: 字幕编号必须从 1 连续递增（位置 {position} 的编号是 {index}）")
        match = _SRT_TIME.fullmatch(lines[1].strip())
        if not match:
            raise SubtitleCorrectionError(f"{path.name}: 第 {index} 条时间轴无效：{lines[1]!r}")
        text = "\n".join(lines[2:]).strip()
        if not text:
            raise SubtitleCorrectionError(f"{path.name}: 第 {index} 条字幕为空")
        start, end = match.group("start"), match.group("end")
        start_ms, end_ms = _timestamp_ms(start), _timestamp_ms(end)
        if end_ms <= start_ms:
            raise SubtitleCorrectionError(f"{path.name}: 第 {index} 条结束时间不晚于开始时间")
        cues.append(SourceCue(index, start, end, start_ms, end_ms, text))
    return cues


def extract_japanese_track(cues: list[SourceCue]) -> list[SourceCue]:
    """Extract track one from JianYing's JA-then-ZH bilingual SRT layout."""
    if len(cues) % 2:
        raise SubtitleCorrectionError("双语字幕总条数不是偶数，无法安全拆分日文/中文轨道")
    half = len(cues) // 2
    first, second = cues[:half], cues[half:]
    first_timeline = [(cue.start, cue.end) for cue in first]
    second_timeline = [(cue.start, cue.end) for cue in second]
    if first_timeline != second_timeline:
        raise SubtitleCorrectionError("双语字幕前后两轨的条数或时间轴不一致，拒绝猜测日文轨道")
    first_kana = sum(bool(_KANA.search(cue.text)) for cue in first)
    second_kana = sum(bool(_KANA.search(cue.text)) for cue in second)
    if first_kana < max(3, half // 10) or second_kana * 4 >= first_kana:
        raise SubtitleCorrectionError("无法确认前半轨是日文、后半轨是中文，拒绝继续")
    return [SourceCue(i, cue.start, cue.end, cue.start_ms, cue.end_ms, cue.text) for i, cue in enumerate(first, 1)]


def load_episode_context(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SubtitleCorrectionError(f"缺少同名日文简介：{path}")
    info = parse_synopsis_md(path.read_text(encoding="utf-8"))
    if not info.get("title") and not info.get("synopsis"):
        raise SubtitleCorrectionError(f"日文简介无法解析：{path}")
    return {"title": info.get("title", ""), "synopsis": info.get("synopsis", "")[:2000]}


def _json_object(reply: str) -> dict[str, Any]:
    text = reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise SubtitleCorrectionError("模型没有返回 JSON 对象")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SubtitleCorrectionError(f"模型 JSON 无法解析：{exc}") from exc
    if not isinstance(value, dict):
        raise SubtitleCorrectionError("模型返回值不是 JSON 对象")
    return value


def _cue_lines(cues: list[SourceCue], start: int, end: int) -> str:
    return "\n".join(f"{cue.source_id}. {' '.join(cue.text.split())}" for cue in cues[start:end])


def _context_section(context: dict[str, str]) -> str:
    sections = [
        f"【剧情背景，仅供理解专名和上下文，严禁从中补写台词】\n日文标题：{context['title']}\n日文简介：{context['synopsis']}"
    ]
    if context.get("zh_title") or context.get("zh_synopsis"):
        sections.append(
            "【中文简介译文，仅供统一译名；与日文原文冲突时以原文为准】\n"
            f"中文标题：{context.get('zh_title', '')}\n中文简介：{context.get('zh_synopsis', '')}"
        )
    return "\n\n".join(sections)


def build_correction_prompt(
    cues: list[SourceCue],
    cursor: int,
    target_end: int,
    window_end: int,
    context_cues: int,
    episode_context: dict[str, str],
) -> str:
    sections = [_context_section(episode_context)]
    context_start = max(0, cursor - context_cues)
    if context_start < cursor:
        sections.append("【只读上文，严禁再次输出】\n" + _cue_lines(cues, context_start, cursor))
    sections.append(
        "【本批必须处理】\n"
        + _cue_lines(cues, cursor, target_end)
        + "\n\n必须完整覆盖以上 ID；若最后一句延续到下方，可继续合并必要的后续 ID。"
    )
    if target_end < window_end:
        sections.append("【可选后续，仅在完成上一句确有必要时使用】\n" + _cue_lines(cues, target_end, window_end))
    sections.append("现在只输出规定的 JSON 对象：")
    return "\n\n".join(sections)


def _parse_ids(value: Any, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, int) for item in value):
        raise SubtitleCorrectionError(f"{label} 必须是非空整数数组")
    ids = tuple(value)
    if ids != tuple(range(ids[0], ids[-1] + 1)):
        raise SubtitleCorrectionError(f"{label} 必须连续：{list(ids)}")
    return ids


def parse_correction_reply(
    reply: str,
    cues: list[SourceCue],
    cursor: int,
    target_end: int,
    window_end: int,
) -> tuple[list[FixedCue], list[ReviewQuestion], int]:
    data = _json_object(reply)
    groups = data.get("groups")
    if not isinstance(groups, list) or not groups:
        raise SubtitleCorrectionError("模型 groups 必须是非空数组")
    expected = cursor + 1
    fixed: list[FixedCue] = []
    for group in groups:
        if not isinstance(group, dict):
            raise SubtitleCorrectionError("groups 中存在非对象条目")
        ids = _parse_ids(group.get("source_ids"), "source_ids")
        text = group.get("text")
        if ids[0] != expected:
            raise SubtitleCorrectionError(f"source_ids 未连续覆盖，期待 {expected}，得到 {ids[0]}")
        if ids[-1] > window_end:
            raise SubtitleCorrectionError(f"模型使用了窗口外 ID：{ids[-1]}")
        if not isinstance(text, str) or not text.strip():
            raise SubtitleCorrectionError(f"ID {list(ids)} 的修正文为空")
        first, last = cues[ids[0] - 1], cues[ids[-1] - 1]
        fixed.append(FixedCue(ids, first.start, last.end, " ".join(text.split())))
        expected = ids[-1] + 1
    consumed = expected - 1
    if consumed < target_end:
        raise SubtitleCorrectionError(f"模型只处理到 {consumed}，本批至少应处理到 {target_end}")

    questions: list[ReviewQuestion] = []
    raw_questions = data.get("questions", [])
    if not isinstance(raw_questions, list):
        raise SubtitleCorrectionError("questions 必须是数组")
    for item in raw_questions:
        if not isinstance(item, dict):
            raise SubtitleCorrectionError("questions 中存在非对象条目")
        ids = _parse_ids(item.get("source_ids"), "question.source_ids")
        reason = item.get("reason")
        if ids[0] <= cursor or ids[-1] > consumed or not isinstance(reason, str) or not reason.strip():
            raise SubtitleCorrectionError("疑问条目的 ID 或原因无效")
        questions.append(ReviewQuestion("日文矫正", ids, reason.strip()))
    return fixed, questions, consumed


def build_translation_prompt(
    cues: list[FixedCue],
    start: int,
    end: int,
    context_cues: int,
    episode_context: dict[str, str],
    memory_note: str = "",
) -> str:
    sections = [memory_note] if memory_note else []
    sections.append(_context_section(episode_context))
    context_start = max(0, start - context_cues)
    if context_start < start:
        sections.append(
            "【只读上文，严禁翻译或输出】\n"
            + "\n".join(f"{i + 1}. {cues[i].text}" for i in range(context_start, start))
        )
    sections.append(
        f"【待翻译，共 {end - start} 条】\n" + "\n".join(f"{i + 1}. {cues[i].text}" for i in range(start, end))
    )
    sections.append("现在只输出上述 ID 的 JSON 对象：")
    return "\n\n".join(sections)


def parse_translation_reply(reply: str, start: int, end: int) -> tuple[list[str], list[ReviewQuestion]]:
    data = _json_object(reply)
    rows = data.get("translations")
    if not isinstance(rows, list):
        raise SubtitleCorrectionError("模型 translations 必须是数组")
    found: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            raise SubtitleCorrectionError("translation 条目格式无效")
        cue_id, text = row["id"], row.get("text")
        if cue_id in found:
            raise SubtitleCorrectionError(f"翻译 ID {cue_id} 重复")
        if not isinstance(text, str) or not text.strip():
            raise SubtitleCorrectionError(f"翻译 ID {cue_id} 为空")
        clean = " ".join(text.split())
        if _KANA.search(clean):
            raise SubtitleCorrectionError(f"翻译 ID {cue_id} 仍含日文假名")
        found[cue_id] = clean
    expected = set(range(start + 1, end + 1))
    if set(found) != expected:
        raise SubtitleCorrectionError(
            f"翻译 ID 不完整，缺少 {sorted(expected - set(found))}，多出 {sorted(set(found) - expected)}"
        )

    questions: list[ReviewQuestion] = []
    raw_questions = data.get("questions", [])
    if not isinstance(raw_questions, list):
        raise SubtitleCorrectionError("questions 必须是数组")
    for item in raw_questions:
        if not isinstance(item, dict):
            raise SubtitleCorrectionError("questions 中存在非对象条目")
        ids = _parse_ids(item.get("ids"), "question.ids")
        reason = item.get("reason")
        if ids[0] <= start or ids[-1] > end or not isinstance(reason, str) or not reason.strip():
            raise SubtitleCorrectionError("翻译疑问条目的 ID 或原因无效")
        questions.append(ReviewQuestion("中文翻译", ids, reason.strip()))
    return [found[i] for i in range(start + 1, end + 1)], questions


class SubtitleCorrectionPipeline:
    def __init__(
        self,
        settings: TranslateSettings,
        chat_fn: Callable[[TranslateSettings, str, str, float], str] | None = None,
        echo: Callable[[str], None] = print,
    ) -> None:
        self.settings = settings
        self._chat_fn = chat_fn or chat
        self._echo = echo
        self._memory = MemoryStore.load(settings.project_dir)

    def _request_json(self, system: str, prompt: str, parser: Callable[[str], Any]) -> Any:
        last_error: Exception | None = None
        retry_prompt = prompt
        for temperature in (0.1, 0.0):
            try:
                reply = self._chat_fn(self.settings, system, retry_prompt, temperature)
                return parser(reply)
            except (SubtitleCorrectionError, KeyError, TypeError, json.JSONDecodeError) as exc:
                last_error = exc
                retry_prompt = (
                    prompt + "\n\n上次输出未通过程序校验：" + str(exc) + "。请重新输出完整且严格合法的 JSON。"
                )
        raise SubtitleCorrectionError(f"本地模型连续两次输出无效：{last_error}")

    def correct_japanese(
        self, cues: list[SourceCue], episode_context: dict[str, str]
    ) -> tuple[list[FixedCue], list[ReviewQuestion]]:
        fixed: list[FixedCue] = []
        questions: list[ReviewQuestion] = []
        cursor = 0
        batch_size = max(5, min(self.settings.batch_size, 30))
        lookahead = max(3, min(self.settings.context_cues, 8))
        while cursor < len(cues):
            target_end = min(cursor + batch_size, len(cues))
            window_end = min(target_end + lookahead, len(cues))
            self._echo(f"[矫正] 原始字幕 {cursor + 1}~{target_end}/{len(cues)}")
            prompt = build_correction_prompt(
                cues,
                cursor,
                target_end,
                window_end,
                self.settings.context_cues,
                episode_context,
            )
            parsed = self._request_json(
                CORRECTION_SYSTEM_PROMPT,
                prompt,
                lambda reply: parse_correction_reply(reply, cues, cursor, target_end, window_end),
            )
            batch_fixed, batch_questions, consumed = parsed
            fixed.extend(batch_fixed)
            questions.extend(batch_questions)
            cursor = consumed
        covered = [source_id for cue in fixed for source_id in cue.source_ids]
        if covered != list(range(1, len(cues) + 1)):
            raise SubtitleCorrectionError("日文修正结果没有逐条覆盖完整原始轨道")
        return fixed, questions

    def translate_chinese(
        self, cues: list[FixedCue], episode_context: dict[str, str]
    ) -> tuple[list[str], list[ReviewQuestion]]:
        translations: list[str] = []
        questions: list[ReviewQuestion] = []
        batch_size = max(5, min(self.settings.batch_size, 30))
        for start in range(0, len(cues), batch_size):
            end = min(start + batch_size, len(cues))
            self._echo(f"[翻译] 修正字幕 {start + 1}~{end}/{len(cues)}")
            batch_text = " ".join(cue.text for cue in cues[start:end])
            memory_note = (
                self._memory.prompt_section(batch_text, max_cases=self.settings.memory_cases)
                if self._memory is not None
                else ""
            )
            prompt = build_translation_prompt(
                cues, start, end, self.settings.context_cues, episode_context, memory_note
            )
            batch_zh, batch_questions = self._request_json(
                TRANSLATION_SYSTEM_PROMPT,
                prompt,
                lambda reply: parse_translation_reply(reply, start, end),
            )
            translations.extend(batch_zh)
            questions.extend(batch_questions)
        if len(translations) != len(cues):
            raise SubtitleCorrectionError("中文字幕数量与修正日文字幕不一致")
        return translations, questions

    def process_file(
        self,
        origin: Path,
        force: bool = False,
        translated_context: dict[str, str] | None = None,
    ) -> CorrectionResult:
        origin = Path(origin)
        if not origin.name.endswith("_origin.srt"):
            raise SubtitleCorrectionError(f"输入文件名必须以 _origin.srt 结尾：{origin.name}")
        base = origin.name.removesuffix("_origin.srt")
        synopsis_path = origin.with_name(f"{base}_jp.md")
        jp_path = origin.with_name(f"{base}_jp_fixed.srt")
        zh_path = origin.with_name(f"{base}_zh_fixed.srt")
        question_path = origin.with_name(f"{base}_translation_questions.md")
        if not force and (jp_path.exists() or zh_path.exists()):
            raise SubtitleCorrectionError(f"输出已存在：{jp_path.name} 或 {zh_path.name}（使用 --force 明确覆盖）")

        source_cues = extract_japanese_track(parse_strict_srt(origin))
        episode_context = load_episode_context(synopsis_path)
        if translated_context is not None:
            episode_context["zh_title"] = translated_context["title"]
            episode_context["zh_synopsis"] = translated_context["synopsis"][:2000]
        fixed, correction_questions = self.correct_japanese(source_cues, episode_context)
        translations, translation_questions = self.translate_chinese(fixed, episode_context)
        questions = correction_questions + translation_questions

        jp_content = render_srt(fixed)
        zh_cues = [
            FixedCue(cue.source_ids, cue.start, cue.end, text) for cue, text in zip(fixed, translations, strict=True)
        ]
        zh_content = render_srt(zh_cues)
        validate_parallel_srt(jp_content, zh_content)
        question_content = render_questions(questions, source_cues, fixed, translations) if questions else None
        outputs: dict[Path, str | None] = {jp_path: jp_content, zh_path: zh_content}
        if question_content is not None or force:
            outputs[question_path] = question_content
        publish_outputs(outputs, force=force)
        return CorrectionResult(
            origin,
            jp_path,
            zh_path,
            question_path if question_content is not None else None,
            len(source_cues),
            len(fixed),
        )


def render_srt(cues: list[FixedCue]) -> str:
    blocks = [f"{index}\n{cue.start} --> {cue.end}\n{cue.text}" for index, cue in enumerate(cues, 1)]
    return "\n\n".join(blocks) + "\n"


def validate_parallel_srt(jp_content: str, zh_content: str) -> None:
    def signature(content: str) -> list[str]:
        result = []
        for block in re.split(r"\n\s*\n", content.strip()):
            lines = block.splitlines()
            if len(lines) < 3 or not lines[0].isdigit() or not _SRT_TIME.fullmatch(lines[1]):
                raise SubtitleCorrectionError("生成的 SRT 格式无效")
            if not "".join(lines[2:]).strip():
                raise SubtitleCorrectionError("生成的 SRT 含空字幕")
            result.append(f"{lines[0]}|{lines[1]}")
        return result

    if signature(jp_content) != signature(zh_content):
        raise SubtitleCorrectionError("日中输出的编号或时间轴不一致")


def render_questions(
    questions: list[ReviewQuestion],
    source: list[SourceCue],
    fixed: list[FixedCue],
    translations: list[str],
) -> str:
    source_by_id = {cue.source_id: cue.text for cue in source}
    fixed_by_source = {
        source_id: (index, cue.text) for index, cue in enumerate(fixed, 1) for source_id in cue.source_ids
    }
    lines = ["# 字幕矫正与翻译存疑", ""]
    for number, question in enumerate(questions, 1):
        if question.stage == "中文翻译":
            fixed_indices = question.ids
            source_ids = tuple(
                source_id for fixed_index in fixed_indices for source_id in fixed[fixed_index - 1].source_ids
            )
            fixed_text = " / ".join(fixed[fixed_index - 1].text for fixed_index in fixed_indices)
            current_zh = " / ".join(translations[fixed_index - 1] for fixed_index in fixed_indices)
            display_index = ", ".join(map(str, fixed_indices))
        else:
            source_ids = question.ids
            fixed_index, fixed_text = fixed_by_source[question.ids[0]]
            current_zh = translations[fixed_index - 1]
            display_index = str(fixed_index)
        raw = " / ".join(source_by_id[item] for item in source_ids)
        lines += [
            f"## {number}. {question.stage}（修正后字幕 #{display_index}）",
            "",
            f"- 原始 ID：{', '.join(map(str, source_ids))}",
            f"- raw ASR：{raw}",
            f"- 修正日文：{fixed_text}",
            f"- 当前中文：{current_zh}",
            f"- 不确定原因：{question.reason}",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def publish_outputs(outputs: dict[Path, str | None], force: bool) -> None:
    existing = [path for path, content in outputs.items() if content is not None and path.exists()]
    if existing and not force:
        raise SubtitleCorrectionError("输出已存在，拒绝覆盖：" + ", ".join(path.name for path in existing))

    temporary: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for path, content in outputs.items():
            if content is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary[path] = Path(handle.name)
        for path in outputs:
            if path.exists() and force:
                backup = path.with_name(f".{path.name}.{uuid.uuid4().hex}.bak")
                path.replace(backup)
                backups[path] = backup
        for path, temp_path in temporary.items():
            temp_path.replace(path)
            published.append(path)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        for path, backup in backups.items():
            if backup.exists():
                backup.replace(path)
        raise
    finally:
        for temp_path in temporary.values():
            temp_path.unlink(missing_ok=True)
