"""Generate the Douyin 3:4 portrait cover for Doraemon stories and record it in metadata.

The cover is rendered from the 剪映 cover template (see
``src/creator_agent/doraemon/jianying_cover.py``) and written next to
``metadata.json`` as ``<story_id>_douyin_portrait.jpg``; the relative filename
goes into ``publish.douyin.cover_portrait_path``.

Run it after the Chinese title exists (``02`` or ``06``), i.e. before publishing:

    python scripts/make_douyin_covers.py "%USERPROFILE%\\Desktop\\doraemon_942" --dry-run
    python scripts/make_douyin_covers.py "%USERPROFILE%\\Desktop\\doraemon_942" --story 942_1
    python scripts/make_douyin_covers.py            # last materials dir, all stories
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from creator_agent.doraemon.covers import (  # noqa: E402
    cover_plan,
    existing_cover,
    load,
    set_portrait_cover,
    stories_to_render,
)
from creator_agent.doraemon.jianying_cover import (  # noqa: E402
    CoverRenderError,
    render_portrait_cover,
    requirements_problem,
)
from creator_agent.doraemon.metadata import MetadataError, metadata_path  # noqa: E402


def choose_target(value: str | None) -> Path:
    if value:
        return metadata_path(Path(value))
    state = REPO_ROOT / "logs/.last_doraemon_materials"
    default = state.read_text(encoding="utf-8-sig").strip() if state.is_file() else ""
    if not default:
        raise MetadataError("请指定素材目录或 metadata.json")
    print(f"默认素材目录：{default}")
    return metadata_path(Path(default))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="为哆啦A梦故事生成抖音竖版封面并写回 metadata.json")
    parser.add_argument("dir", nargs="?", help="素材目录或 metadata.json；默认使用上次素材目录")
    parser.add_argument("--story", action="append", help="只处理指定故事，如 942_1；可重复")
    parser.add_argument("--force", action="store_true", help="已有封面也重新生成并覆盖路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要生成什么，不动剪映也不写文件")
    args = parser.parse_args()

    try:
        path = choose_target(args.dir)
        data = load(path)
        stories = stories_to_render(data, args.story)
    except (MetadataError, OSError, UnicodeError) as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return 2

    print("=" * 50)
    print(f"  metadata : {path}")
    print(f"  集号     : {int(data['episode'])}")
    print(f"  故事     : {', '.join(story['story_id'] for story in stories)}")
    print("=" * 50)

    problem = requirements_problem()
    rendered = skipped = failed = 0
    for story in stories:
        story_id = story["story_id"]
        try:
            plan = cover_plan(path, story, force=args.force)
            if plan is None:
                cover = existing_cover(path, story)
                print(f"  SKIP {story_id}：已有封面 {cover.relative_to(path.parent).as_posix() if cover else ''}")
                skipped += 1
                continue
            target, title = plan
            print(f"  MAKE {story_id}：标题「{title}」→ {target.name}")
            if args.dry_run:
                print("       （dry-run，未生成）")
                continue
            if problem:
                raise CoverRenderError(problem)
            render_portrait_cover(
                image=path.parent / story["image_file"], episode=str(int(data["episode"])), title=title, out=target
            )
            set_portrait_cover(path, story_id, target, overwrite=args.force)
            print(f"  OK   {story_id} → publish.douyin.cover_portrait_path = {target.name}")
            rendered += 1
        except (CoverRenderError, MetadataError, OSError) as exc:
            print(f"  FAIL {story_id}：{exc}", file=sys.stderr)
            failed += 1

    print("-" * 50)
    print(f"生成 {rendered}，跳过 {skipped}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
