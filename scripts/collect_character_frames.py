"""Character material collection tool (Doraemon V1.0).

Reads the scene list produced by scripts/split_scene.py (scenes.json) and
extracts candidate character frames from the ORIGINAL story video — never
from the per-scene MP4s. Does NOT modify the scene-splitting pipeline.

Usage:
    uv run python scripts/collect_character_frames.py --episode 935_1 --preview
    uv run python scripts/collect_character_frames.py --episode 935_1
    uv run python scripts/collect_character_frames.py --episode 935_1 --sync-labels
    uv run python scripts/collect_character_frames.py --scenes-json path/to/scenes.json --video path/to/935_1.mp4

Default input layout (from split_scene.py):
    output/<episode_id>_scenes/scenes.json   +   output/<episode_id>.mp4

Output layout (character_library/):
    inbox/<episode_id>/<candidate_id>.jpg        passed candidate frames
    previews/<episode_id>/contact_sheet_NNN.jpg  paged labelled overviews
    manifests/<episode_id>_candidates.jsonl      full index (source + filters + labels)
    characters/<char>/<candidate_id>.jpg         human-classified COPIES

Workflow: extract -> open contact sheets -> copy good shots into
characters/<char>/ (uncertain ones into characters/unknown/) -> --sync-labels.
Re-running extraction is safe: human classifications are never overwritten.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from creator_agent.character_collection.collector import extract_episode, plan_episode
from creator_agent.character_collection.contact_sheet import build_contact_sheets
from creator_agent.character_collection.manifest import (
    load_manifest,
    manifest_path,
    save_manifest,
    sync_labels,
)
from creator_agent.character_collection.scene_reader import (
    SceneReaderError,
    default_scenes_json,
    load_episode_scenes,
)

# Pre-created character folders (human classification targets). "unknown" is
# for confirmed-but-unidentified characters; everything else maps 1:1 to a
# future characters.json entry.
CHARACTERS = [
    "doraemon",
    "nobita",
    "shizuka",
    "gian",
    "suneo",
    "dorami",
    "nobita_mother",
    "nobita_father",
    "gian_mother",
    "suneo_mother",
    "teacher",
    "unknown",
]


def ensure_library_dirs(library_root: Path) -> None:
    for sub in ("inbox", "manifests", "previews", "analysis"):
        (library_root / sub).mkdir(parents=True, exist_ok=True)
    for char in CHARACTERS:
        (library_root / "characters" / char).mkdir(parents=True, exist_ok=True)


def fmt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def die(msg: str) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    raise SystemExit(1)


def cmd_preview(ep) -> int:
    plan = plan_episode(ep)
    total = sum(count for _, _, count, _ in plan)
    print(f"剧集: {ep.episode_id}")
    print(f"故事视频: {ep.video_path}")
    print(f"分镜清单: {ep.scenes_json}")
    print(f"FPS: {ep.fps:.3f}   分镜数: {len(plan)}   计划候选帧: {total}\n")
    print(f"{'分镜':<8}{'时长(s)':>8}{'抽帧数':>6}  采样时间点")
    for scene_id, duration, count, times in plan:
        pts = " ".join(fmt_time(t) for t in times)
        print(f"{scene_id:<8}{duration:>8.2f}{count:>6}  {pts}")
    return 0


def cmd_extract(ep, library_root: Path) -> int:
    print(f"剧集: {ep.episode_id}   分镜数: {len(ep.scenes)}")
    print(f"故事视频: {ep.video_path}")
    records, stats = extract_episode(ep, library_root)

    print("\n=== 提取统计 ===")
    print(f"分镜: {stats.scenes_total}   计划抽帧: {stats.planned}   通过: {stats.passed}")
    for status, count in sorted(stats.filtered.items()):
        print(f"  {status}: {count}")
    if stats.fully_filtered_scenes:
        print(
            f"全部被过滤/解码失败的分镜 ({len(stats.fully_filtered_scenes)}): " + ", ".join(stats.fully_filtered_scenes)
        )

    print("\n=== 联系表 ===")
    sheets = build_contact_sheets(list(records.values()), library_root, ep.episode_id)
    for s in sheets:
        print(f"  {s}")

    print("\n输出位置:")
    print(f"  候选图片: {library_root / 'inbox' / ep.episode_id}/")
    print(f"  联系表:   {library_root / 'previews' / ep.episode_id}/")
    print(f"  索引:     {manifest_path(library_root, ep.episode_id)}")
    print("\n下一步: 浏览联系表，把确认的人物图片【复制】到 characters/<角色>/，然后运行 --sync-labels")
    return 0


def cmd_sync(library_root: Path, episode_id: str) -> int:
    mpath = manifest_path(library_root, episode_id)
    records = load_manifest(mpath)
    if not records:
        die(f"索引为空或不存在: {mpath}\n请先运行提取: --episode {episode_id}")
    records, report = sync_labels(library_root, episode_id, records)
    save_manifest(mpath, records)

    print(f"剧集: {episode_id}   索引: {mpath}")
    print("\n=== 同步结果 ===")
    print(f"新分类: {report.updated}   已一致: {report.already_ok}")
    reviewed = sum(1 for r in records.values() if r.review_status != "unreviewed")
    print(f"已确认总数: {reviewed} / {len(records)}")
    if report.warnings:
        print(f"\n警告 ({report.warnings} 条，均未改动数据):", file=sys.stderr)
        for msg in report.conflicts + report.unknown_files + report.other_episode + report.missing_files:
            print(f"  - {msg}", file=sys.stderr)
    return 0 if not report.conflicts else 1


def main() -> int:
    p = argparse.ArgumentParser(
        description="从分镜结果提取人物候选帧（不修改分镜切割流程）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--episode", help="剧集 ID = 故事视频文件名 stem，如 935_1")
    p.add_argument(
        "--scenes-json", type=Path, help="显式指定 scenes.json 路径（默认 output/<episode>_scenes/scenes.json）"
    )
    p.add_argument("--video", type=Path, help="显式指定故事视频路径（抽帧来源，默认按 scenes.json 的 source 解析）")
    p.add_argument("--library", type=Path, default=Path("character_library"), help="素材库根目录")
    p.add_argument("--preview", action="store_true", help="只显示抽帧计划，不提取")
    p.add_argument("--sync-labels", action="store_true", help="同步人工分类结果到索引")
    args = p.parse_args()

    ensure_library_dirs(args.library)

    if args.sync_labels:
        if not args.episode:
            die("--sync-labels 需要 --episode")
        return cmd_sync(args.library, args.episode)

    scenes_json = args.scenes_json
    if scenes_json is None:
        if not args.episode:
            die("需要 --episode 或 --scenes-json")
        scenes_json = default_scenes_json(args.episode)
    try:
        ep = load_episode_scenes(scenes_json, video_override=args.video)
    except SceneReaderError as e:
        die(str(e))
    if args.episode and args.episode != ep.episode_id:
        print(
            f"警告: --episode={args.episode} 与 scenes.json source 推导的 {ep.episode_id} 不一致，以 source 为准",
            file=sys.stderr,
        )

    if args.preview:
        return cmd_preview(ep)
    return cmd_extract(ep, args.library)


if __name__ == "__main__":
    raise SystemExit(main())
