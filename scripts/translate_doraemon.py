"""Translate Doraemon Japanese metadata and save Chinese context and platform tags.

Reads ~/Desktop/doraemon_{ep}/metadata.json. No Markdown sidecars are produced.
Uses the configured local OpenAI-compatible server, shared with subtitle processing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import httpx
from fetch_doraemon import desktop_episode_dir

from creator_agent.config import load_settings
from creator_agent.doraemon.metadata import (
    MetadataError,
    get_story,
    load_metadata,
    save_tags,
    save_translation,
    source_hash,
    translation_is_current,
    validate_tags,
)
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
6. 只输出翻译结果，不添加任何解释
7. 保留简介的段落和换行，不合并成单段"""

_KANA = re.compile(r"[぀-ヿ]")

TAG_SYSTEM_PROMPT = """你是一名短视频运营专家，深谙抖音/B站上《哆啦A梦》粉丝圈的流量密码，为剧集视频生成话题标签。

受众定位：经常刷哆啦A梦视频的老粉 —— 标签要用这个圈子平时会搜、会关注、一看就想点的热词，
不要书面化的剧情概括。

要求：
1. 总共 8~11 个标签，最精准、最适合抖音的标签放在前 5 个
2. 固定流量标签 2~4 个，按契合度从粉丝圈高热词中挑：
   #哆啦A梦 #哆啦A梦新番 #蓝胖子 #童年回忆 #童年回忆杀 #治愈系 #搞笑日常 #藤子F不二雄
3. 本集内容标签 5~8 个，必须用粉丝圈"行话"，让人一眼认出是哪一集、并且想点进去：
   - 道具用粉丝熟知的叫法：任意门、竹蜻蜓、时光机、记忆面包、缩小灯、如果电话亭……
   - 角色/圈内热梗风格：#胖虎开演唱会 #小夫又在炫富 #大雄这次赢麻了 #大雄和静香发糖
   - 剧情钩子制造好奇和情绪：#道具失控 #结局泪目 #全程高能 #神反转 #大雄被坑惨了
4. 每个标签 2~12 个汉字，不含空格、标点、日文假名
5. 严禁整组都是 #动漫 #动画 #二次元 这类空泛标签
6. 只输出一行，以"标签："开头，各标签以 # 开头、用空格分隔，不输出任何其他内容"""

_TAG_LINE = re.compile(r"^\s*标签\s*[:：]\s*(.+?)\s*$")
_TAG_TOKEN = re.compile(r"#([^\s#，,。、；;！!？?]+)")

# Generic filler that adds no reach but makes the set look lazy; dropped when
# the model ignores rule 4 and pads with them anyway.
_GENERIC_TAGS = {"动漫", "动画", "二次元", "动画片", "日本动漫", "日本动画", "动漫推荐"}


def build_tag_prompt(zh_title: str, zh_synopsis: str) -> str:
    return f"根据下面的中文标题和简介生成发布标签：\n\n标题：{zh_title}\n简介：\n{zh_synopsis}"


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
    return tags[:11]


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


def translate_metadata(path: Path, story_id: str, settings, refresh: bool = False) -> tuple[str, str, bool]:
    story = get_story(load_metadata(path), story_id)
    if not story["title"].strip() or not story["synopsis"].strip():
        raise MetadataError(f"{story_id} 日文标题或简介为空")
    expected_hash = source_hash(story)
    current = translation_is_current(story)
    if current and not refresh:
        zh_title, zh_synopsis = story["zh"]["title"], story["zh"]["synopsis"]
    else:
        zh_title, zh_synopsis = translate_story(settings, story["title"], story["synopsis"])
        save_translation(path, story_id, expected_hash, zh_title, zh_synopsis)
        print(f"  translate : {story_id} -> metadata.json  {zh_title}")

    if current and not refresh:
        try:
            for platform, maximum in (("douyin", 5), ("bilibili", 11)):
                validate_tags(story["publish"][platform]["tags"], maximum)
            print(f"  reuse     : {story_id} 中文简介和标签")
            return zh_title, zh_synopsis, True
        except MetadataError:
            pass

    try:
        tags = generate_tags(settings, zh_title, zh_synopsis)
        validate_tags(tags, 11)
    except Exception as exc:
        # A tag-service failure must not discard a saved synopsis or stop subtitles.
        print(f"  TAG FAILED: {story_id} ({exc})")
        return zh_title, zh_synopsis, False
    save_tags(path, story_id, expected_hash, zh_title, zh_synopsis, tags)
    print(f"  tags      : {story_id} -> metadata.json")
    return zh_title, zh_synopsis, True


def main(episode: str, refresh: bool = False) -> None:
    if not episode.strip().isdigit():
        print(f"Invalid episode number: {episode!r} (expected e.g. 934)")
        sys.exit(2)
    path = desktop_episode_dir(str(int(episode))) / "metadata.json"
    try:
        data = load_metadata(path)
        if int(data["episode"]) != int(episode):
            raise MetadataError("目录 metadata 的集号与请求不匹配")
    except MetadataError as exc:
        print(f"Run scripts/fetch_doraemon.py first: {exc}")
        sys.exit(1)

    settings = load_settings().translate
    failed = 0
    for story in data["stories"]:
        try:
            _, _, tags_ok = translate_metadata(path, story["story_id"], settings, refresh=refresh)
        except (ValueError, httpx.HTTPError, OSError) as exc:
            failed += 1
            print(f"  FAILED    : {story['story_id']} ({exc})")
            continue
        if not tags_ok:
            failed += 1
    if failed:
        print(f"\n{failed}/{len(data['stories'])} stories failed to translate.")
        sys.exit(1)
    print(f"\nTranslated {len(data['stories'])} stories into {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="翻译 metadata 中的日文简介并保存平台标签")
    parser.add_argument("episode")
    parser.add_argument("--refresh-translation", action="store_true", help="重新翻译中文简介并重建两平台标签")
    args = parser.parse_args()
    main(args.episode, refresh=args.refresh_translation)
