"""Translate Doraemon episode notes (title + synopsis) JA -> ZH.

Reads the per-story notes written by ``fetch_doraemon.py``
(``<vault>/简介/{ep}_{i}.md``, '# <title>' + synopsis body) and writes a
Chinese sibling next to each one::

    <vault>/简介/{ep}_{i}_zh.md   # '# <中文标题>' + 中文简介

Uses the same local OpenAI-compatible LLM server as the subtitle translator
(``translate`` section of config/settings.yaml — llama.cpp Qwen3.5-9B by
default). The server must already be running; bat/doraemon.bat starts it via
:ensure_server before invoking this script.

Usage:
    uv run python scripts/translate_doraemon.py 934
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

# scripts/ is on sys.path when run as `python scripts/translate_doraemon.py`,
# so the vault dir stays single-sourced in fetch_doraemon.py.
from fetch_doraemon import VAULT_SYNOPSIS_DIR

from creator_agent.config import load_settings
from creator_agent.translate.llm import chat

# Make non-ASCII print correctly on the Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SYSTEM_PROMPT = """你是一名专业日中翻译，负责翻译动漫《哆啦A梦》的剧集标题与故事简介。

要求：
1. 自然流畅，符合中文母语者的表达习惯，不要机械直译
2. 不增添原文没有的信息，不遗漏信息
3. 人名统一：ドラえもん→哆啦A梦、のび太→大雄、しずか→静香、ジャイアン→胖虎、スネ夫→小夫
4. 秘密道具名翻译简洁统一
5. 只输出翻译结果，不添加任何解释"""

_KANA = re.compile(r"[぀-ヿ]")
_TITLE_LINE = re.compile(r"^\s*标题\s*[:：]\s*(.+?)\s*$")
_SYNOPSIS_LINE = re.compile(r"^\s*简介\s*[:：]\s*(.*)$")


def parse_note(path: Path) -> tuple[str, str]:
    """'# <title>' + body -> (title, synopsis)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"{path.name}: expected '# <title>' on the first line")
    return lines[0][2:].strip(), "\n".join(lines[1:]).strip()


def build_user_prompt(title: str, synopsis: str) -> str:
    return (
        "把下面的日文标题和简介翻译成中文，严格按以下格式输出"
        "（“简介：”单独占一行，简介正文可分段）：\n\n"
        "标题：<中文标题>\n"
        "简介：\n"
        "<中文简介>\n\n"
        "—— 以下是日文原文，仅供翻译，严禁原样输出 ——\n"
        f"标题：{title}\n"
        "简介：\n"
        f"{synopsis}"
    )


def parse_reply(reply: str) -> tuple[str, str]:
    """Extract (zh_title, zh_synopsis) from the model reply.

    Raises ValueError when the format is wrong or the reply still contains
    kana (model echoed Japanese instead of translating) — caller retries.
    """
    lines = reply.splitlines()
    title: str | None = None
    body_lines: list[str] | None = None
    for idx, line in enumerate(lines):
        if title is None:
            m = _TITLE_LINE.match(line)
            if m:
                title = m.group(1)
            continue
        m = _SYNOPSIS_LINE.match(line)
        if m:
            body_lines = ([m.group(1)] if m.group(1).strip() else []) + lines[idx + 1 :]
            break
    if title is None or body_lines is None:
        raise ValueError("reply does not follow the 标题：/简介： format")
    title = title.strip().strip("「」")
    body = "\n".join(body_lines).strip()
    if not title or not body:
        raise ValueError("reply has an empty title or synopsis")
    if _KANA.search(title) or _KANA.search(body):
        raise ValueError("reply still contains Japanese kana (echoed, not translated)")
    return title, body


def translate_story(settings, title: str, synopsis: str) -> tuple[str, str]:
    """Translate one story, retrying once on an unparseable/echoed reply."""
    user = build_user_prompt(title, synopsis)
    last_err: ValueError | None = None
    for temperature in (0.2, 0.0):
        reply = chat(settings, SYSTEM_PROMPT, user, temperature=temperature)
        try:
            return parse_reply(reply)
        except ValueError as exc:
            last_err = exc
    raise ValueError(f"translation failed after retry: {last_err}")


def main(episode: str) -> None:
    if not episode.strip().isdigit():
        print(f"Invalid episode number: {episode!r} (expected e.g. 934)")
        sys.exit(2)
    ep_short = str(int(episode))

    notes = sorted(
        p for p in VAULT_SYNOPSIS_DIR.glob(f"{ep_short}_*.md") if not p.stem.endswith("_zh")
    )
    if not notes:
        print(f"No notes matching {ep_short}_*.md in {VAULT_SYNOPSIS_DIR}")
        print("Run scripts/fetch_doraemon.py first.")
        sys.exit(1)

    settings = load_settings().translate
    failed = 0
    for note in notes:
        zh_path = note.with_name(f"{note.stem}_zh.md")
        try:
            title, synopsis = parse_note(note)
            zh_title, zh_synopsis = translate_story(settings, title, synopsis)
        except (ValueError, httpx.HTTPError) as exc:
            failed += 1
            print(f"  FAILED    : {note.name} ({exc})")
            continue
        out_lines = [f"# {zh_title}", ""] + zh_synopsis.splitlines()
        zh_path.write_text("\n".join(out_lines).strip() + "\n", encoding="utf-8")
        print(f"  translate : {note.name} -> {zh_path.name}  {zh_title}")

    if failed:
        print(f"\n{failed}/{len(notes)} stories failed to translate.")
        sys.exit(1)
    print(f"\nTranslated {len(notes)} stories into {VAULT_SYNOPSIS_DIR}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: translate_doraemon.py <episode_number>  (e.g. 934)")
        sys.exit(2)
    main(sys.argv[1])
