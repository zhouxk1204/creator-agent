"""JA -> ZH subtitle translation via a local OpenAI-compatible LLM server.

You are a professional JA->ZH subtitle translator — see ``SYSTEM_PROMPT``.
The backend is any OpenAI-compatible chat-completions endpoint (Ollama /
llama.cpp / vLLM / LM Studio), so the model (Qwen3.5 9B by default) runs
wherever the user installed it; this module only needs httpx.

Batching / prompt building / response parsing are pure functions, unit-tested
without a server. ``SrtTranslator`` does the I/O: read ``<stem>.srt`` ->
translate in batches of ``batch_size`` cues (with a few leading cues of
untranslated context) -> write ``<stem>.zh.srt`` with the original indices
and timeline untouched.

Robustness contract: a batch whose reply doesn't parse or has the wrong
numbering is retried once, then split in half recursively; a single cue that
still fails keeps its Japanese text (logged) rather than aborting the file.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from creator_agent.asr.subtitle import parse_srt, write_srt
from creator_agent.models.transcript import TranscriptSegment

if TYPE_CHECKING:
    from creator_agent.config import TranslateSettings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一名专业日中字幕翻译。

将日文字幕翻译成自然、准确、符合中文母语者习惯的中文字幕。

要求：
1. 不增添原文没有的信息
2. 不遗漏信息
3. 保留人物语气、情绪和上下文
4. 日语口语不要机械直译
5. 根据上下文处理省略主语
6. 人名、地名、专有名词保持统一
7. 中文字幕简洁，适合视频阅读
8. 不改变字幕编号
9. 不改变时间轴
10. 只输出翻译结果，不添加解释"""

_LINE = re.compile(r"^\s*(\d+)\s*[.、．:：]\s*(.+?)\s*$")
_KANA = re.compile(r"[぀-ヿ]")


def _mostly_japanese(texts: list[str]) -> bool:
    """True when over half the translated cues still contain kana — i.e. the
    model paraphrased/echoed Japanese instead of translating. A stray kana in
    one cue (a kept loanword) does not trip this."""
    if len(texts) < 2:
        return False
    return sum(1 for t in texts if _KANA.search(t)) > len(texts) / 2


class TranslationMismatchError(ValueError):
    """The model's reply can't be mapped 1:1 onto the batch's cue indices."""


def make_batches(n_cues: int, batch_size: int) -> list[tuple[int, int]]:
    """Consecutive ``[start, end)`` windows of at most ``batch_size`` cues."""
    if batch_size < 1 or n_cues < 1:
        return []
    return [(i, min(i + batch_size, n_cues)) for i in range(0, n_cues, batch_size)]


def build_prompt(
    segments: list[TranscriptSegment],
    start: int,
    end: int,
    context_cues: int = 5,
    memory_note: str = "",
    synopsis: dict | None = None,
) -> str:
    """User message for one batch: optional learned-memory section, optional
    episode synopsis (story context), up to ``context_cues`` preceding cues
    as reference-only context, then the numbered cues to translate.

    Prompt shape matters: the 9B model echoes Japanese when the context block
    reads like "here is text, don't translate it". What works (verified
    against llama.cpp Qwen3.5-9B): an explicit "forbidden to translate/output"
    context header, a to-translate header demanding Chinese output, and a
    closing instruction line after the cues."""
    lines: list[str] = []
    if memory_note:
        lines += [memory_note, ""]
    if synopsis and (synopsis.get("title") or synopsis.get("synopsis")):
        head = f"【剧情背景】本集《{synopsis['title']}》" if synopsis.get("title") else "【剧情背景】本集"
        lines.append(f"{head}故事简介，仅供理解上下文，严禁翻译、严禁输出：")
        if synopsis.get("synopsis"):
            lines.append(synopsis["synopsis"])
        lines.append("")
    ctx = segments[max(0, start - context_cues) : start]
    if ctx:
        lines.append("【背景参考】以下日语原文仅供理解上下文，严禁翻译、严禁输出：")
        lines += [f"{i + 1}. {_one_line(s.text)}" for i, s in enumerate(ctx, start - len(ctx))]
        lines.append("")
    lines.append(
        f"【待翻译，共 {end - start} 条】把下面日文字幕翻译成中文，"
        "严格按编号逐条输出「编号. 中文译文」，译文必须是中文："
    )
    lines += [f"{i + 1}. {_one_line(segments[i].text)}" for i in range(start, end)]
    lines += ["", "现在只输出上述编号的中文翻译："]
    return "\n".join(lines)


def parse_translation(reply: str, start: int, end: int) -> list[str]:
    """Map a model reply onto cue indices ``[start, end)`` (0-based; the reply
    uses 1-based cue numbers). Raises ``TranslationMismatchError`` when any cue is
    missing or duplicated. Extra/garbage lines are ignored."""
    found: dict[int, str] = {}
    for line in reply.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        idx, text = int(m.group(1)), m.group(2)
        if start + 1 <= idx <= end:
            if idx in found:
                raise TranslationMismatchError(f"cue {idx} translated twice")
            found[idx] = text
    missing = [i for i in range(start + 1, end + 1) if i not in found]
    if missing:
        raise TranslationMismatchError(f"missing translations for cues {missing}")
    return [found[i] for i in range(start + 1, end + 1)]


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _progress(msg: str) -> None:
    print(f"[translate] {msg}", file=sys.stderr, flush=True)
    logger.info(msg)


class SrtTranslator:
    def __init__(self, settings: TranslateSettings) -> None:
        self._s = settings

    # -- LLM backend --------------------------------------------------------

    def _chat(self, user: str) -> str:
        from creator_agent.translate.llm import chat

        return chat(self._s, SYSTEM_PROMPT, user)

    # -- translation ----------------------------------------------------------

    def _memory(self):
        """Learned-corrections store (lazy; None when no memory exists yet)."""
        if not hasattr(self, "_store"):
            from creator_agent.translate.memory import MemoryStore

            self._store = MemoryStore.load(self._s.project_dir)
        return self._store

    def translate_segments(
        self, segments: list[TranscriptSegment], tag: str = "", synopsis: dict | None = None
    ) -> list[str]:
        """Translate every segment's text; returns a parallel list of Chinese
        strings. Indices/timestamps are the caller's business. ``synopsis``
        (from ``vault.load_synopsis``) is injected into every batch's prompt
        as story context."""
        zh = [""] * len(segments)
        batches = make_batches(len(segments), self._s.batch_size)
        for bi, (start, end) in enumerate(batches):
            _progress(f"{tag}批次 {bi + 1}/{len(batches)}：字幕 {start + 1}~{end} 翻译中…")
            self._translate_batch(segments, start, end, zh, tag, synopsis)
        return zh

    def _translate_batch(
        self,
        segments: list[TranscriptSegment],
        start: int,
        end: int,
        zh: list[str],
        tag: str,
        synopsis: dict | None = None,
    ) -> None:
        memory_note = ""
        store = self._memory()
        if store is not None:
            batch_text = " ".join(_one_line(segments[i].text) for i in range(start, end))
            memory_note = store.prompt_section(batch_text, max_cases=self._s.memory_cases)
        prompt = build_prompt(segments, start, end, self._s.context_cues, memory_note=memory_note, synopsis=synopsis)
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                if attempt > 0:
                    prompt += "\n\n（上次输出编号不完整或仍是日文，请重新输出全部编号的中文翻译）"
                reply = self._chat(prompt)
                translated = parse_translation(reply, start, end)
                if _mostly_japanese(translated):
                    raise TranslationMismatchError("reply is still Japanese, not translated")
                zh[start:end] = translated
                return
            except (TranslationMismatchError, httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                logger.warning("batch %d-%d attempt %d failed: %s", start, end, attempt + 1, e)
        if end - start > 1:
            mid = (start + end) // 2
            _progress(f"{tag}  批次 {start + 1}~{end} 解析失败（{last_err}），拆半重试")
            self._translate_batch(segments, start, mid, zh, tag, synopsis)
            self._translate_batch(segments, mid, end, zh, tag, synopsis)
        else:
            logger.warning("cue %d keeps original text: %s", start + 1, last_err)
            _progress(f"{tag}  ! 第 {start + 1} 条翻译失败，保留原文（{last_err}）")
            zh[start] = segments[start].text

    # -- file level -----------------------------------------------------------

    def translate_srt(self, srt_path: Path, out_path: Path) -> Path:
        from creator_agent.translate.vault import load_synopsis

        segments = parse_srt(srt_path)
        if not segments:
            raise RuntimeError(f"no cues parsed from {srt_path}")
        synopsis = load_synopsis(self._s.project_dir, srt_path.stem)
        if synopsis:
            _progress(f"[{srt_path.stem}] 参考简介：《{synopsis['title']}》")
        zh = self.translate_segments(segments, tag=f"[{srt_path.stem}] ", synopsis=synopsis)
        zh_segments = [TranscriptSegment(start=s.start, end=s.end, text=t) for s, t in zip(segments, zh)]
        return write_srt(zh_segments, out_path)


def process_translations(
    paths: list[Path],
    settings: TranslateSettings,
    out_dir: Path | None = None,
    burn: bool = False,
    force: bool = False,
) -> tuple[int, list[tuple[str, str]]]:
    """Translate (and optionally burn) the SRT of each video/.srt in ``paths``.
    Per-file failures never abort the batch. Returns ``(done, [(name, err)])``."""
    from creator_agent.asr.ja_transcriber import VIDEO_EXTS
    from creator_agent.translate.burner import burn_subtitles

    done = 0
    failed: list[tuple[str, str]] = []
    for i, p in enumerate(paths):
        p = Path(p)
        _progress(f"===== 文件 {i + 1}/{len(paths)}：{p.name} =====")
        zh_srt, err = translate_video_srt(p, settings, out_dir=out_dir, force=force)
        if err:
            failed.append((p.name, err))
            continue
        if burn:
            video = p if p.suffix.lower() in VIDEO_EXTS else _find_video_for_srt(p)
            if video is None or not video.exists():
                failed.append((p.name, f"no video found next to {p.name} to burn into"))
                continue
            try:
                out_video = (Path(out_dir) if out_dir else video.parent) / f"{video.stem}.zh.mp4"
                burn_subtitles(video, zh_srt, out_video, settings)
            except Exception as e:
                logger.exception("burn failed for %s", video)
                failed.append((video.name, str(e)))
                continue
        done += 1
    return done, failed


def _find_video_for_srt(srt_path: Path) -> Path | None:
    from creator_agent.asr.ja_transcriber import VIDEO_EXTS

    for ext in VIDEO_EXTS:
        cand = srt_path.with_suffix(ext)
        if cand.exists():
            return cand
    return None


def translate_video_srt(
    video_or_srt: Path, settings: TranslateSettings, out_dir: Path | None = None, force: bool = False
) -> tuple[Path | None, str | None]:
    """Translate the ``<stem>.srt`` belonging to a video (or a given .srt)
    into ``<stem>.zh.srt``. Returns ``(zh_srt_path, None)`` or ``(None, error)``;
    an up-to-date existing .zh.srt is returned as-is unless ``force``.
    When ``settings.export_subtitles`` is on, the JA/ZH srts are also mirrored
    into ``<project_dir>/<stem>/`` (copy-if-newer)."""
    from creator_agent.translate.vault import export_subtitles

    p = Path(video_or_srt)
    srt = p if p.suffix.lower() == ".srt" else p.with_suffix(".srt")
    if not srt.exists():
        return None, f"subtitle not found: {srt}"
    target_dir = Path(out_dir) if out_dir else srt.parent
    zh_srt = target_dir / f"{srt.stem}.zh.srt"
    if zh_srt.exists() and not force:
        _progress(f"[{srt.stem}] 已有 {zh_srt.name}，跳过（--force 重翻）")
        if settings.export_subtitles:
            export_subtitles(settings.project_dir, srt.stem, [srt, zh_srt])
        return zh_srt, None
    try:
        SrtTranslator(settings).translate_srt(srt, zh_srt)
        _progress(f"[{srt.stem}] 翻译完成 → {zh_srt.name}")
        if settings.export_subtitles:
            export_subtitles(settings.project_dir, srt.stem, [srt, zh_srt])
        return zh_srt, None
    except Exception as e:
        logger.exception("translation failed for %s", srt)
        return None, str(e)
