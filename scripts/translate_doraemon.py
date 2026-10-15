"""Translate Doraemon episode notes (title + synopsis) JA -> ZH, plus tags.

Reads the per-story notes written by ``fetch_doraemon.py``
(``<vault>/简介/{ep}/{ep}_{i}.md``, '# <title>' + cover embed + synopsis body)
and writes a Chinese sibling next to each one::

    <vault>/简介/{ep}/{ep}_{i}_zh.md   # '# <中文标题>' + cover embed + 中文简介
                                      # + 最后一行 '标签：#xx #yy ...'（发布话题标签）

Tags are generated from the *translated* title+synopsis (Douyin/Bilibili style
hashtags): a couple of base hot tags plus story-specific ones (characters,
props, plot hooks) — never just generic "#动漫 #动画" filler.

Uses the same local OpenAI-compatible LLM server as the subtitle translator
(``translate`` section of config/settings.yaml — llama.cpp Qwen3.5-9B by
default). The server must already be running; bat/doraemon/02_fetch_doraemon.bat starts it via
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
5. 拟声词、效果音也要翻译成中文拟声词（如 ハクション→阿嚏），译文中不要保留日文假名
6. 只输出翻译结果，不添加任何解释"""

_KANA = re.compile(r"[぀-ヿ]")
_COVER_EMBED = re.compile(r"^\s*!\[\[.+\]\]\s*$")

TAG_SYSTEM_PROMPT = """你是一名短视频运营专家，为《哆啦A梦》剧集视频生成发布话题标签（抖音 / B站风格）。

要求：
1. 总共 8~12 个标签
2. 基础热门标签 2~3 个：结合剧集气质挑选，如 #哆啦A梦 #新番 #童年动画 #治愈系动画 #搞笑动画
3. 内容专属标签占其余全部（至少 5 个）：必须来自本集剧情 —— 出场角色名、秘密道具名、
   核心情节关键词、情绪/看点钩子（如 #大雄逆袭 #时光机失控 #静香的秘密）
4. 严禁整组都是 #动漫 #动画 #二次元 这类空泛标签；每个内容标签都要让人一眼看出是"这一集"
5. 每个标签 2~12 个汉字，不含空格、标点、日文假名
6. 只输出一行，以"标签："开头，各标签以 # 开头、用空格分隔，不输出任何其他内容"""

_TAG_LINE = re.compile(r"^\s*标签\s*[:：]\s*(.+?)\s*$")
_TAG_TOKEN = re.compile(r"#([^\s#，,。、；;！!？?]+)")

# Generic filler that adds no reach but makes the set look lazy; dropped when
# the model ignores rule 4 and pads with them anyway.
_GENERIC_TAGS = {"动漫", "动画", "二次元", "动画片", "日本动漫", "日本动画", "动漫推荐"}


def build_tag_prompt(zh_title: str, zh_synopsis: str) -> str:
    return (
        "根据下面的中文标题和简介生成发布标签：\n\n"
        f"标题：{zh_title}\n"
        "简介：\n"
        f"{zh_synopsis}"
    )


def parse_tags(reply: str) -> list[str]:
    """Extract the tag list from the model reply.

    Accepts a '标签：#a #b ...' line (or any line carrying #tokens), drops
    generic filler and kana-contaminated tags, dedupes, and requires at
    least 5 surviving tags — else ValueError so the caller retries.
    """
    raw: str | None = None
    for line in reply.splitlines():
        m = _TAG_LINE.match(line)
        if m:
            raw = m.group(1)
            break
        if _TAG_TOKEN.search(line) and raw is None:
            raw = line
    if raw is None:
        raise ValueError("reply has no 标签： line")

    tags: list[str] = []
    for token in _TAG_TOKEN.findall(raw):
        token = token.strip()
        if not token or _KANA.search(token):
            continue
        if len(token) > 12:
            continue
        if token in _GENERIC_TAGS:
            continue
        if token not in tags:
            tags.append(token)
    if len(tags) < 5:
        raise ValueError(f"too few usable tags ({len(tags)}): {tags}")
    return tags[:12]


def generate_tags(settings, zh_title: str, zh_synopsis: str) -> list[str]:
    """Generate publish tags for one story, retrying once on a bad reply."""
    user = build_tag_prompt(zh_title, zh_synopsis)
    last_err: ValueError | None = None
    for temperature in (0.6, 0.3):
        reply = chat(settings, TAG_SYSTEM_PROMPT, user, temperature=temperature)
        try:
            return parse_tags(reply)
        except ValueError as exc:
            last_err = exc
    raise ValueError(f"tag generation failed after retry: {last_err}")


def _too_much_kana(text: str) -> bool:
    """True when the reply is substantially Japanese (echoed, not translated).

    A few kept kana — an onomatopoeia like ハクション quoted inside an
    otherwise fully Chinese sentence — do not trip this.
    """
    kana = len(_KANA.findall(text))
    return kana > 10 or (kana > 0 and kana / len(text) > 0.1)
_TITLE_LINE = re.compile(r"^\s*标题\s*[:：]\s*(.+?)\s*$")
_SYNOPSIS_LINE = re.compile(r"^\s*简介\s*[:：]\s*(.*)$")


def parse_note(path: Path) -> tuple[str, str, str | None]:
    """'# <title>' + optional '![[封面/...]]' + body -> (title, synopsis, cover).

    The cover embed is pulled out of the body so it is never sent to the
    model; the caller re-inserts it into the translated sibling.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"{path.name}: expected '# <title>' on the first line")
    cover: str | None = None
    body_lines: list[str] = []
    for line in lines[1:]:
        if _COVER_EMBED.match(line):
            cover = cover or line.strip()
            continue
        body_lines.append(line)
    return lines[0][2:].strip(), "\n".join(body_lines).strip(), cover


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
    if _too_much_kana(title) or _too_much_kana(body):
        raise ValueError("reply is still mostly Japanese (echoed, not translated)")
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

    note_dir = VAULT_SYNOPSIS_DIR / ep_short
    notes = sorted(p for p in note_dir.glob(f"{ep_short}_*.md") if not p.stem.endswith("_zh"))
    if not notes:
        print(f"No notes matching {ep_short}_*.md in {note_dir}")
        print("Run scripts/fetch_doraemon.py first.")
        sys.exit(1)

    settings = load_settings().translate
    failed = 0
    for note in notes:
        zh_path = note.with_name(f"{note.stem}_zh.md")
        try:
            title, synopsis, cover = parse_note(note)
            zh_title, zh_synopsis = translate_story(settings, title, synopsis)
        except (ValueError, httpx.HTTPError) as exc:
            failed += 1
            print(f"  FAILED    : {note.name} ({exc})")
            continue

        # Tags ride along with the translation; a tag failure still writes the
        # translated file (without the tag line) but marks the story failed so
        # the run reports it and the user can re-run.
        tags: list[str] = []
        tag_err: Exception | None = None
        try:
            tags = generate_tags(settings, zh_title, zh_synopsis)
        except (ValueError, httpx.HTTPError) as exc:
            tag_err = exc

        out_lines = [f"# {zh_title}", ""]
        if cover:
            out_lines += [cover, ""]
        out_lines += zh_synopsis.splitlines()
        if tags:
            out_lines += ["", "标签：" + " ".join(f"#{t}" for t in tags)]
        zh_path.write_text("\n".join(out_lines).strip() + "\n", encoding="utf-8")
        if tag_err is None:
            print(f"  translate : {note.name} -> {zh_path.name}  {zh_title}  [{len(tags)} tags]")
        else:
            failed += 1
            print(f"  translate : {note.name} -> {zh_path.name}  {zh_title}")
            print(f"  TAG FAILED: {note.name} ({tag_err})")

    if failed:
        print(f"\n{failed}/{len(notes)} stories failed to translate.")
        sys.exit(1)
    print(f"\nTranslated {len(notes)} stories into {note_dir}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: translate_doraemon.py <episode_number>  (e.g. 934)")
        sys.exit(2)
    main(sys.argv[1])
