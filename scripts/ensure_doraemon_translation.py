"""Publish gate: make sure zh.synopsis and platform tags exist before uploading.

``bat/doraemon/03_publish.bat`` runs this before any platform step, so a story
that only carries Japanese metadata can never reach the uploader. Only stories
that already have media to publish are checked: a story that has not been
exported yet must not block the ones that are.

Exit codes, relied on by the launcher:

    0  ready (with --check: nothing missing)
    1  translation or tag generation failed
    2  metadata problem (missing or broken metadata.json)
    4  --check only: at least one story still needs translation/tags
    5  translation LLM unreachable (the launcher starts it and runs again)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from translate_doraemon import translate_metadata  # noqa: E402  (scripts-dir import, needs fetch_doraemon)

from creator_agent.config import load_settings  # noqa: E402
from creator_agent.doraemon.metadata import (  # noqa: E402
    MetadataError,
    load_metadata,
    translation_is_current,
    validate_tags,
)
from scripts.publish_dual import b_videos, choose_target, dy_videos  # noqa: E402

READY_RC = 0
FAILED_RC = 1
METADATA_RC = 2
MISSING_RC = 4
LLM_UNREACHABLE_RC = 5

# Same caps the publisher validates with.
PLATFORM_LIMITS = {"douyin": 5, "bilibili": 11}


def platforms_in_play(only: str | None) -> tuple[str, ...]:
    return (only,) if only else ("douyin", "bilibili")


def stories_to_check(path: Path, data: dict, args: argparse.Namespace) -> list[dict]:
    """Stories that actually have media to publish, in metadata order.

    With no ``--b``/``--dy`` flag (the shared prepare step in 03_publish.bat
    runs before the platform is chosen) the media found on disk decides, so an
    episode that is only half exported cannot stall the part that is ready.
    """
    platforms = platforms_in_play(args.only)
    batch = args.b or args.dy
    ids: set[str] = set()
    if args.b or not batch:
        if "bilibili" in platforms:
            ids |= {story_id for story_id, _ in b_videos(path.parent, data["stories"])}
    if args.dy or not batch:
        if "douyin" in platforms:
            ids |= {story_id for story_id, _ in dy_videos(path.parent, data["stories"])}
    if args.story:
        ids = {story_id for story_id in ids if story_id == args.story}
    if not ids:
        return []
    return [story for story in data["stories"] if story["story_id"] in ids]


def pending_work(story: dict, platforms: tuple[str, ...]) -> str:
    """What still has to be generated, e.g. ``中文简介、抖音标签``; ``""`` when ready."""
    if not translation_is_current(story):
        return "中文简介"
    lacking = []
    for platform in platforms:
        try:
            validate_tags(story["publish"][platform]["tags"], PLATFORM_LIMITS[platform])
        except (KeyError, MetadataError):
            lacking.append(f"{platform}标签")
    return "、".join(lacking)


def describe(story: dict, work: str) -> str:
    title = (story.get("zh") or {}).get("title") or story.get("title") or ""
    return f"{story['story_id']}（{title}）：{work}"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="发布前确保中文简介和平台标签存在，缺失时先自动翻译")
    parser.add_argument("dir", nargs="?", help="素材目录或 metadata.json；默认使用 logs/.last_doraemon_materials")
    parser.add_argument("--story", help="只检查待发布的该故事，如 943_1")
    parser.add_argument("--b", action="store_true", help="按素材目录根下的 _b 视频判断待发布故事")
    parser.add_argument("--dy", action="store_true", help="按 <故事>_dy/ 里的剪映导出判断待发布故事")
    parser.add_argument("--only", choices=["douyin", "bilibili"], help="只检查一个平台的标签")
    parser.add_argument("--check", action="store_true", help="只报告缺什么，不翻译；有缺失时退出码 4")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要翻译什么，不调用翻译模型")
    parser.add_argument("--ask", "--prompt-target", action="store_true", help="强制询问素材目录")
    parser.add_argument("--headed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    for name in ("--tid", "--schedule", "--douyin-account", "--bilibili-account"):
        parser.add_argument(name, help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        path = choose_target(args.dir, args.ask)
        data = load_metadata(path)
        stories = stories_to_check(path, data, args)
        platforms = platforms_in_play(args.only)
        pending = [(story, pending_work(story, platforms)) for story in stories]
        pending = [(story, work) for story, work in pending if work]
    except (MetadataError, OSError, UnicodeError, EOFError) as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return METADATA_RC

    print("=" * 50)
    print(f"  metadata : {path}")
    if not stories:
        print("  待发布   : （没有 _b / _dy 素材，本次无需翻译）")
    else:
        print(f"  待发布   : {', '.join(story['story_id'] for story in stories)}")
        for story, work in pending:
            print(f"  待补     : {describe(story, work)}")
    print("=" * 50)

    if not pending:
        if stories:
            print("  中文简介和标签都已就绪，可以发布。")
        return READY_RC
    if args.dry_run:
        # Never translate (or start the model) in a dry run: publishing is not
        # going to happen, and --check adds nothing once we are already reporting.
        print("[!] dry-run：以上内容需要先翻译，正式发布会自动补上。")
        return READY_RC
    if args.check:
        return MISSING_RC

    settings = load_settings().translate
    failed = 0
    for story, work in pending:
        print(f"  翻译 {story['story_id']}：{work}")
        try:
            _, _, tags_ok = translate_metadata(path, story["story_id"], settings)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            print(f"[X] 翻译模型连不上（{exc}）；先启动 bat\\start_translate_server.bat", file=sys.stderr)
            return LLM_UNREACHABLE_RC
        except (MetadataError, ValueError, httpx.HTTPError, OSError) as exc:
            print(f"  FAILED {story['story_id']}：{exc}", file=sys.stderr)
            failed += 1
            continue
        if not tags_ok:
            print(f"  FAILED {story['story_id']}：标签生成失败", file=sys.stderr)
            failed += 1

    if failed:
        print(f"[X] {failed}/{len(pending)} 个故事翻译失败，未进入发布。", file=sys.stderr)
        return FAILED_RC

    # Re-read from disk: a translation that silently failed to persist must not
    # let the uploader start with empty metadata.
    try:
        left = [(story, pending_work(story, platforms)) for story in stories_to_check(path, load_metadata(path), args)]
        left = [(story, work) for story, work in left if work]
    except MetadataError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return METADATA_RC
    if left:
        for story, work in left:
            print(f"[X] 仍然缺少：{describe(story, work)}", file=sys.stderr)
        return FAILED_RC
    print("  翻译完成，继续发布。")
    return READY_RC


if __name__ == "__main__":
    raise SystemExit(main())
