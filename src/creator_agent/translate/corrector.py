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
from creator_agent.doraemon.metadata import (
    MetadataError,
    load_metadata,
    story_for_subtitle,
    translation_is_current,
)
from creator_agent.translate.llm import chat
from creator_agent.translate.memory import MemoryStore

_SRT_TIME = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
)
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]")

# The correction stage asks the local 9B model for one plain line per subtitle
# instead of a nested JSON object: with the JSON schema it kept closing the
# object after the first sentence (or folding the whole batch into one group),
# while the line protocol covers a full 30-cue batch reliably.
#
# It is strictly one line per source cue: the human re-splits the episode by
# hand in JianYing, so any merge the model makes costs them manual work and
# changes the timeline they rely on.
_ID_RANGE = r"(\d+)(?:\s*[~〜\-–—,]\s*(\d+))?"
_CORRECTION_LINE = re.compile(rf"^{_ID_RANGE}\s*[|｜]\s*(.+)$")
_CORRECTION_QUESTION = re.compile(rf"^[?？]\s*{_ID_RANGE}\s*[|｜]\s*(.+)$")

# A batch the model failed twice is split in half and retried instead of
# failing the whole episode; below this size (or this recursion depth) a
# failure is real and the file stops.
_MIN_SPLIT_CUES = 6
_MAX_SPLIT_DEPTH = 3

# The model routinely stops one or two cues short of a batch end, and splitting
# cannot fix that (a 4-cue batch failed the same way). The missing cues are
# simply kept as raw ASR, which is the honest result of "no confident fix".
_MAX_TAIL_FILL = 2

# The local model sometimes reads a name in the Japanese line as the *speaker*
# and renders it with that speaker's Chinese name (941: 「頼んだぞ、のび太」 came
# back as 「拜托了，胖虎」). See enforce_names below.
_NOBITA = "のび太"
_NOBITA_ZH = "大雄"
_OTHER_SPEAKERS = ("ジャイアン", "ギュミ")

CORRECTION_SYSTEM_PROMPT = """你是《哆啦A梦》日语字幕审校员。输入来自剪映日语 ASR，没有音频可听。

任务：逐条纠正高置信度 ASR 误识，并修正日语的断句和标点。条数和时间轴由程序决定，不得更改。
规则：
1. 忠实保留原始措辞、语序和口语；不得根据剧情简介脑补原字幕没有的台词。
2. 简介只用于确认人物、地点、道具、剧情背景；与 ASR 冲突时以 ASR 为准。
3. 只修正上下文能唯一确定的音近误识、人物名和专名；有多个合理候选时保留原文并记录疑问。
   人名按读音判断：ASR 会把「のび太」写成读音相同的汉字词，例如「伸びたんだも」其实是
   「のび太だも」、「チェイア」其实是「ジャイアン」。上下文明确是人名时按人名改正。
4. 禁止合并、禁止拆分：一条原始条目只对应一条修正结果，不许并到相邻条目，也不许拆成两条。
   一个句子被剪映切在相邻两条时，各自只修好自己那半句（前半句用「、」或「…」收尾，
   不硬加句末标点），两条连起来读通顺即可。本条是残句就保持残句，不得把下一条的
   词句补进本条，也不得凭剧情把本条补成完整句。
5. 必须覆盖给定的每一个 ID，每个 ID 恰好出现一次；不得创造、丢失或重复 ID；
   不得只输出开头的几句就结束。
6. 标点用日文全角：句末「。」「！」「？」，句内停顿「、」。
7. 不输出时间轴，不输出解释，不要 Markdown 代码块。

输出格式：一条原始条目一行，`ID|修正后的日语`，不接受区间写法（不得写 `12-14|…`）。
真正的疑问单独一行，`?ID|无法唯一判断的原因`。
除这些行以外不要输出任何内容。"""

TRANSLATION_SYSTEM_PROMPT = """你是《哆啦A梦》专业日中字幕翻译。

任务：把已审校的日文字幕逐条翻译为自然、简洁的中文字幕。
规则：
1. 忠实逐条翻译，不增添、遗漏或解释原文信息；保留人物语气和情绪。
2. 人名统一：ドラえもん→哆啦A梦、のび太→大雄、しずか→静香、ジャイアン→胖虎、スネ夫→小夫。
3. 人物、地点、道具和固定梗在整集中保持一致；简介只作为背景，不得覆盖字幕。
4. 每个输入 ID 恰好输出一次，不能合并或拆分；译文不得残留日文假名。
5. 原文被剪映切得很碎，一条常常只是半句：每条只译自己这一条，不补上一句的内容，
   但用词要让相邻两条连读成一句通顺的口语（不要把整句意思重复写进每条）。
6. 只输出 JSON，不要 Markdown 代码块或解释。

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


def load_episode_context(origin: Path) -> dict[str, str]:
    try:
        story = story_for_subtitle(load_metadata(origin.parent / "metadata.json"), origin)
        if not translation_is_current(story):
            raise MetadataError(f"{story['story_id']} 中文简介缺失或已过期，请先翻译 metadata")
        return {
            "title": story["title"],
            "synopsis": story["synopsis"][:2000],
            "zh_title": story["zh"]["title"],
            "zh_synopsis": story["zh"]["synopsis"][:2000],
        }
    except MetadataError as exc:
        raise SubtitleCorrectionError(str(exc)) from exc


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


def _plain_lines(cues: list[SourceCue], start: int, end: int) -> str:
    """Context text without IDs, so the model cannot mirror them into its output."""
    return "\n".join(" ".join(cue.text.split()) for cue in cues[start:end])


def enforce_names(jp_text: str, zh_text: str) -> str:
    """Rewrite a name the model swapped for the speaker's Chinese name.

    In 941 the local model rendered 「頼んだぞ、のび太」 as 「拜托了，胖虎」 (five
    cues) because it attributed the line to Giant. Once the Japanese cue names
    のび太 and no other character, no other rendering is possible, so the wrong
    name is replaced instead of trusting the prompt again.
    """
    if _NOBITA in jp_text and _NOBITA_ZH not in zh_text and "胖虎" in zh_text:
        if not any(name in jp_text for name in _OTHER_SPEAKERS):
            return zh_text.replace("胖虎", _NOBITA_ZH)
    return zh_text


_GUARD_STRIP = re.compile(r"[\s、。！？…「」『』【】・,.\-—～~!?\"'()（）:;]")

# 941 cue 8: ASR heard 「のび太」 as the homophone 「伸びた」. Only the sentence
# pattern 「伸びた(ん)だも(ん)」 is rewritten, so the real verb 「背が伸びた」
# stays untouched.
_NOMINAL_MISREAD = re.compile(r"^(伸びた|延びた|のびた|ノビタ)(ん)?(だもん?|だろ|だね|だよ|だ|じゃ)")
_NOMINAL_CONTEXT = ("背", "身長", "髪", "丈", "成績")

# A cue may gain a little (a dropped kana, a kana written as kanji) but never
# a whole clause: that is the model importing the neighbouring line's words.
_MAX_CUE_GROWTH = 3

# Below this, the "fix" shares too few characters with the raw cue to be the
# same sentence at all (941: 「よーし新しい」 came back as 「僕を見つけるぞ」).
_MIN_CUE_OVERLAP = 0.4


def _visible_len(text: str) -> int:
    return len(_GUARD_STRIP.sub("", text))


def _bare(text: str) -> str:
    return _GUARD_STRIP.sub("", text)


def guard_correction(raw: str, fixed: str, neighbours: tuple[str, ...] = ()) -> str:
    """Keep the raw cue when the "correction" is damage or padding.

    Three shapes showed up in the 941 runs and none is an ASR correction:
    dropping characters (「のびのび太だろ」→「びのび太だろ」), swapping one
    kana for another (「勝させてあげよう」→「勝らせてあげよう」, less correct
    than the raw) and pasting the next cue's words in (「よし留」→
    「よし、留学先が登録された」, a merge in disguise). Punctuation, kana→kanji
    and a couple of added characters still pass, so real fixes survive.
    """
    if fixed == raw:
        return fixed
    if _visible_len(fixed) < _visible_len(raw):
        return raw
    if _visible_len(fixed) > _visible_len(raw) + _MAX_CUE_GROWTH:
        return raw
    # 「相変わらず単」→「相変わらず単純だね」: the two added characters are the
    # next cue's opening 「純だねギュミは」, i.e. a merge the growth cap let
    # through. Only a tail of 2+ characters is checked, so a 1-character fix
    # like 「のび」→「のび太」 still passes.
    if fixed.startswith(raw):
        tail = _bare(fixed[len(raw) :])
        if len(tail) >= 2 and any(_bare(other).startswith(tail) for other in neighbours):
            return raw
    if fixed.endswith(raw):
        head = _bare(fixed[: len(fixed) - len(raw)])
        if len(head) >= 2 and any(_bare(other).endswith(head) for other in neighbours):
            return raw
    raw_chars = set(_bare(raw))
    if raw_chars and len(raw_chars & set(_bare(fixed))) / len(raw_chars) < _MIN_CUE_OVERLAP:
        return raw
    a = [char for char in raw if not char.isspace()]
    b = [char for char in fixed if not char.isspace()]
    if len(a) == len(b):
        diffs = [index for index, (left, right) in enumerate(zip(a, b)) if left != right]
        if len(diffs) == 1 and _KANA.match(a[diffs[0]]) and _KANA.match(b[diffs[0]]):
            return raw
    return fixed


def repair_known_misreads(text: str) -> str:
    """Fix homophone names the model leaves alone.

    In 941 the model kept 「伸びたんだも」 because it reads as a plausible verb
    phrase; only a deterministic rewrite gets it back to 「のび太だも」.
    """
    if any(word in text for word in _NOMINAL_CONTEXT):
        return text
    match = _NOMINAL_MISREAD.match(text)
    if not match:
        return text
    tail = match.group(3)
    return _NOBITA + tail + text[match.end() :]


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
    context_cues: int,
    episode_context: dict[str, str],
) -> str:
    sections = [_context_section(episode_context)]
    context_start = max(0, cursor - context_cues)
    if context_start < cursor:
        # No IDs here: when the read-only cues were numbered, the model started
        # its answer from the context's first ID instead of the batch start.
        sections.append("【前文，仅供理解，没有 ID，禁止输出】\n" + _plain_lines(cues, context_start, cursor))
    sections.append(
        f"【待处理】共 {target_end - cursor} 条，ID {cursor + 1}~{target_end}，必须全部处理，不得提前结束\n"
        + _cue_lines(cues, cursor, target_end)
        + "\n\n每条只修自己这一句，不合并、不拆分，行数必须等于条目数。"
    )
    sections.append(
        f"现在逐行输出，第一行必须是最小 ID {cursor + 1}，最后一行必须是 ID {target_end}，"
        "一条原始条目一行，写作 `ID|修正后的日语`："
    )
    return "\n\n".join(sections)


def _parse_ids(value: Any, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, int) for item in value):
        raise SubtitleCorrectionError(f"{label} 必须是非空整数数组")
    ids = tuple(value)
    if ids != tuple(range(ids[0], ids[-1] + 1)):
        raise SubtitleCorrectionError(f"{label} 必须连续：{list(ids)}")
    return ids


def _reply_lines(reply: str) -> list[str]:
    text = reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return [line.strip() for line in text.splitlines()]


def _line_range(match: re.Match[str]) -> tuple[int, int]:
    first = int(match.group(1))
    last = int(match.group(2)) if match.group(2) else first
    if last < first:
        raise SubtitleCorrectionError(f"ID 区间反向：{first}-{last}")
    return first, last


def parse_correction_lines(
    reply: str,
    cues: list[SourceCue],
    cursor: int,
    target_end: int,
) -> tuple[list[FixedCue], list[ReviewQuestion], int]:
    """Parse `ID|text` lines and verify one line per source cue, in order.

    Ranges are rejected: merging cues would silently change the JianYing
    timeline the human re-splits by hand.
    """
    fixed: list[FixedCue] = []
    pending_questions: list[tuple[tuple[int, ...], str]] = []
    expected = cursor + 1
    for line in _reply_lines(reply):
        if not line:
            continue
        question = _CORRECTION_QUESTION.match(line)
        if question:
            first, last = _line_range(question)
            if first <= cursor:
                continue  # doubt about read-only context: advisory only
            pending_questions.append((tuple(range(first, last + 1)), question.group(3).strip()))
            continue
        match = _CORRECTION_LINE.match(line)
        if not match:
            raise SubtitleCorrectionError(f"模型输出了无法解析的行：{line[:60]}")
        first, last = _line_range(match)
        if first != expected:
            raise SubtitleCorrectionError(f"ID 未连续覆盖，期待 {expected}，得到 {first}")
        if last != first:
            raise SubtitleCorrectionError(f"模型合并了 {first}-{last}，本流程禁止合并，一条只能对一条")
        if last > target_end:
            raise SubtitleCorrectionError(f"模型使用了本批之外的 ID：{last}")
        text = match.group(3)
        if not text.strip():
            raise SubtitleCorrectionError(f"ID {first} 的修正文为空")
        fixed.append(FixedCue((first,), cues[first - 1].start, cues[first - 1].end, " ".join(text.split())))
        expected = last + 1

    consumed = expected - 1
    if not fixed:
        raise SubtitleCorrectionError("模型没有输出任何修正行")
    if consumed < target_end:
        if target_end - consumed > _MAX_TAIL_FILL:
            raise SubtitleCorrectionError(f"模型只处理到 {consumed}，本批至少应处理到 {target_end}")
        # Keep the tail as raw ASR instead of failing the batch: the human
        # re-splits by the original timeline anyway, and an unedited cue is
        # still a correct cue.
        for cue_id in range(consumed + 1, target_end + 1):
            source = cues[cue_id - 1]
            fixed.append(FixedCue((cue_id,), source.start, source.end, source.text))
        consumed = target_end

    questions: list[ReviewQuestion] = []
    for ids, reason in pending_questions:
        # Advisory only: a doubt line that reaches past this batch (or has no
        # reason) is dropped rather than failing subtitle content that is fine.
        if not reason or ids[-1] > consumed:
            continue
        questions.append(ReviewQuestion("日文矫正", ids, reason))
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
            "【前文，仅供理解，没有 ID，禁止输出】\n"
            + "\n".join(" ".join(cues[i].text.split()) for i in range(context_start, start))
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
    expected = set(range(start + 1, end + 1))
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            raise SubtitleCorrectionError("translation 条目格式无效")
        cue_id, text = row["id"], row.get("text")
        if cue_id not in expected:
            # The model sometimes runs past the batch and translates the next
            # batch's opening cues; those lines are simply not ours to keep.
            continue
        if cue_id in found:
            raise SubtitleCorrectionError(f"翻译 ID {cue_id} 重复")
        if not isinstance(text, str) or not text.strip():
            raise SubtitleCorrectionError(f"翻译 ID {cue_id} 为空")
        clean = " ".join(text.split())
        if _KANA.search(clean):
            raise SubtitleCorrectionError(f"翻译 ID {cue_id} 仍含日文假名")
        found[cue_id] = clean
    if set(found) != expected:
        raise SubtitleCorrectionError(f"翻译 ID 不完整，缺少 {sorted(expected - set(found))}")

    questions: list[ReviewQuestion] = []
    raw_questions = data.get("questions", [])
    if not isinstance(raw_questions, list):
        raise SubtitleCorrectionError("questions 必须是数组")
    for item in raw_questions:
        if not isinstance(item, dict):
            raise SubtitleCorrectionError("questions 中存在非对象条目")
        ids = _parse_ids(item.get("ids"), "question.ids")
        reason = item.get("reason")
        if ids[0] <= start or ids[-1] > end:
            continue  # doubt about a neighbouring batch: advisory only
        if not isinstance(reason, str) or not reason.strip():
            continue
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

    def _request_checked(self, system: str, prompt: str, parser: Callable[[str], Any]) -> Any:
        last_error: Exception | None = None
        retry_prompt = prompt
        for temperature in (0.1, 0.0):
            try:
                reply = self._chat_fn(self.settings, system, retry_prompt, temperature)
                return parser(reply)
            except (SubtitleCorrectionError, KeyError, TypeError, json.JSONDecodeError) as exc:
                last_error = exc
                retry_prompt = (
                    prompt
                    + "\n\n上次输出未通过程序校验："
                    + str(exc)
                    + "。请严格按要求重新输出完整结果，覆盖全部给定 ID。"
                )
        raise SubtitleCorrectionError(f"本地模型连续两次输出无效：{last_error}")

    def _correct_block(
        self,
        cues: list[SourceCue],
        cursor: int,
        target_end: int,
        episode_context: dict[str, str],
        depth: int,
    ) -> tuple[list[FixedCue], list[ReviewQuestion], int]:
        self._echo(f"[矫正] 原始字幕 {cursor + 1}~{target_end}/{len(cues)}")
        prompt = build_correction_prompt(
            cues,
            cursor,
            target_end,
            self.settings.context_cues,
            episode_context,
        )
        try:
            return self._request_checked(
                CORRECTION_SYSTEM_PROMPT,
                prompt,
                lambda reply: parse_correction_lines(reply, cues, cursor, target_end),
            )
        except SubtitleCorrectionError as exc:
            span = target_end - cursor
            if span <= _MIN_SPLIT_CUES or depth >= _MAX_SPLIT_DEPTH:
                raise
            middle = cursor + span // 2
            self._echo(f"[拆分] {cursor + 1}~{target_end} 两次无效（{exc}），改为 {cursor + 1}~{middle} 与后续分段")
            left_fixed, left_questions, left_end = self._correct_block(cues, cursor, middle, episode_context, depth + 1)
            right_fixed, right_questions, right_end = self._correct_block(
                cues, left_end, target_end, episode_context, depth + 1
            )
            return left_fixed + right_fixed, left_questions + right_questions, right_end

    def correct_japanese(
        self, cues: list[SourceCue], episode_context: dict[str, str]
    ) -> tuple[list[FixedCue], list[ReviewQuestion]]:
        fixed: list[FixedCue] = []
        questions: list[ReviewQuestion] = []
        cursor = 0
        batch_size = max(5, min(self.settings.batch_size, 30))
        while cursor < len(cues):
            target_end = min(cursor + batch_size, len(cues))
            batch_fixed, batch_questions, consumed = self._correct_block(cues, cursor, target_end, episode_context, 0)
            fixed.extend(batch_fixed)
            questions.extend(batch_questions)
            cursor = consumed
        covered = [source_id for cue in fixed for source_id in cue.source_ids]
        if covered != list(range(1, len(cues) + 1)):
            raise SubtitleCorrectionError("日文修正结果没有逐条覆盖完整原始轨道")
        if len(fixed) != len(cues):
            raise SubtitleCorrectionError(f"日文修正结果条数 {len(fixed)} 不等于原始条数 {len(cues)}（不允许合并）")
        guarded = 0
        repaired = 0
        for index, (cue, target) in enumerate(zip(cues, fixed, strict=True)):
            neighbours = tuple(cues[other].text for other in (index - 1, index + 1) if 0 <= other < len(cues))
            kept = guard_correction(cue.text, target.text, neighbours)
            # Runs on the guarded text, so a cue the model left untouched but
            # ASR wrote as a homophone is still repaired (941 cue 8).
            named = repair_known_misreads(kept)
            if kept != target.text:
                guarded += 1
            if named != kept:
                repaired += 1
            if named != target.text:
                fixed[index] = FixedCue(target.source_ids, target.start, target.end, named)
        if guarded:
            self._echo(f"[校验] 回退误改 {guarded} 处（模型减字、补写邻句或单假名替换，不属于 ASR 修正）")
        if repaired:
            self._echo(f"[校验] 人名读音修正 {repaired} 处（如同音汉字被写成「伸びた」）")
        return fixed, questions

    def _translate_block(
        self,
        cues: list[FixedCue],
        start: int,
        end: int,
        episode_context: dict[str, str],
        depth: int,
    ) -> tuple[list[str], list[ReviewQuestion]]:
        self._echo(f"[翻译] 修正字幕 {start + 1}~{end}/{len(cues)}")
        batch_text = " ".join(cue.text for cue in cues[start:end])
        memory_note = (
            self._memory.prompt_section(batch_text, max_cases=self.settings.memory_cases)
            if self._memory is not None
            else ""
        )
        prompt = build_translation_prompt(cues, start, end, self.settings.context_cues, episode_context, memory_note)
        try:
            return self._request_checked(
                TRANSLATION_SYSTEM_PROMPT,
                prompt,
                lambda reply: parse_translation_reply(reply, start, end),
            )
        except SubtitleCorrectionError as exc:
            span = end - start
            if span <= _MIN_SPLIT_CUES or depth >= _MAX_SPLIT_DEPTH:
                raise
            middle = start + span // 2
            self._echo(f"[拆分] 翻译 {start + 1}~{end} 两次无效（{exc}），改为 {start + 1}~{middle} 与后续分段")
            left_texts, left_questions = self._translate_block(cues, start, middle, episode_context, depth + 1)
            right_texts, right_questions = self._translate_block(cues, middle, end, episode_context, depth + 1)
            return left_texts + right_texts, left_questions + right_questions

    def translate_chinese(
        self, cues: list[FixedCue], episode_context: dict[str, str]
    ) -> tuple[list[str], list[ReviewQuestion]]:
        translations: list[str] = []
        questions: list[ReviewQuestion] = []
        batch_size = max(5, min(self.settings.batch_size, 30))
        for start in range(0, len(cues), batch_size):
            end = min(start + batch_size, len(cues))
            batch_zh, batch_questions = self._translate_block(cues, start, end, episode_context, 0)
            translations.extend(batch_zh)
            questions.extend(batch_questions)
        guard_hits = 0
        for index, cue in enumerate(cues):
            cleaned = enforce_names(cue.text, translations[index])
            if cleaned != translations[index]:
                translations[index] = cleaned
                guard_hits += 1
        if guard_hits:
            self._echo(f"[校验] 人名归一 {guard_hits} 处（原文のび太，模型误用说话人的名字）")
        if len(translations) != len(cues):
            raise SubtitleCorrectionError("中文字幕数量与修正日文字幕不一致")
        return translations, questions

    def process_file(self, origin: Path, force: bool = False) -> CorrectionResult:
        origin = Path(origin)
        if not origin.name.endswith("_origin.srt"):
            raise SubtitleCorrectionError(f"输入文件名必须以 _origin.srt 结尾：{origin.name}")
        base = origin.name.removesuffix("_origin.srt")
        jp_path = origin.with_name(f"{base}_jp_fixed.srt")
        zh_path = origin.with_name(f"{base}_zh_fixed.srt")
        question_path = origin.with_name(f"{base}_translation_questions.md")
        if not force and (jp_path.exists() or zh_path.exists()):
            raise SubtitleCorrectionError(f"输出已存在：{jp_path.name} 或 {zh_path.name}（使用 --force 明确覆盖）")

        source_cues = extract_japanese_track(parse_strict_srt(origin))
        episode_context = load_episode_context(origin)
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
