"""Publish one Doraemon metadata story to Douyin/Bilibili via social-auto-upload.

This entry point and its metadata dependency use only the standard library,
so they also run inside social-auto-upload's separate virtual environment.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from creator_agent.doraemon.metadata import (  # noqa: E402
    MetadataError,
    get_story,
    load_metadata,
    metadata_path,
    resolve_asset,
    translation_is_current,
    validate_tags,
)

SAU_ROOT = REPO_ROOT / "tools" / "social-auto-upload"
VIDEO_EXTS = {".mp4", ".mov", ".flv", ".mkv", ".avi"}
COVER_EXTS = {".jpg", ".jpeg", ".png"}
DEFAULT_DOUYIN_DECLARATION = "虚构演绎，仅供娱乐"
DEFAULT_DOUYIN_COLLECTION = "哆啦A梦2026合集"


def sau_exe() -> Path:
    return SAU_ROOT / ".venv" / ("Scripts/sau.exe" if sys.platform == "win32" else "bin/sau")


def required_asset(path: Path, value, extensions: set[str]) -> Path:
    asset = resolve_asset(path, value)
    if not asset.is_file() or asset.suffix.lower() not in extensions:
        raise MetadataError(f"素材不存在或格式不支持：{asset}")
    return asset


class PublishJob:
    def __init__(self, target: Path, args: argparse.Namespace):
        self.path = metadata_path(target)
        data = load_metadata(self.path)
        story_id = args.story
        if not story_id:
            if len(data["stories"]) != 1:
                raise MetadataError("有多个故事，请用 --story 941_1 明确选择一个")
            story_id = data["stories"][0]["story_id"]
        story = get_story(data, story_id)
        if not translation_is_current(story):
            raise MetadataError(f"{story_id} 中文翻译缺失或已过期，请先运行 06 或 translate_doraemon.py")
        self.story_id = story_id
        publish = story["publish"]
        self.title = publish["title"]
        if not isinstance(self.title, str) or not self.title.strip() or "\n" in self.title or "\r" in self.title:
            raise MetadataError("publish.title 必须是非空单行标题")
        prefix = f"哆啦a梦{int(data['episode'])} "
        if not self.title.startswith(prefix) or not self.title[len(prefix) :].strip():
            raise MetadataError(f"publish.title 格式应为：{prefix}中文标题")
        self.video = required_asset(self.path, publish["video_path"], VIDEO_EXTS)
        self.do_douyin = args.only in (None, "douyin")
        self.do_bilibili = args.only in (None, "bilibili")
        self.schedule = args.schedule or publish.get("schedule")
        self.douyin_account = args.douyin_account
        self.bilibili_account = args.bilibili_account
        self.cover_douyin_portrait = self.cover_douyin_landscape = self.cover_bilibili = None
        self.douyin_desc = self.bilibili_desc = ""
        self.douyin_tags = self.bilibili_tags = ""
        self.tid = None
        if self.do_douyin:
            if len(self.title) > 55:
                raise MetadataError("抖音标题最多 55 字，请编辑 publish.title")
            douyin = publish["douyin"]
            tags = validate_tags(douyin["tags"], 5)
            self.douyin_tags = ",".join(tags)
            self.douyin_desc = " ".join(f"#{tag}" for tag in tags)
            self.cover_douyin_portrait = required_asset(self.path, douyin["cover_portrait_path"], COVER_EXTS)
            self.cover_douyin_landscape = required_asset(self.path, douyin["cover_landscape_path"], COVER_EXTS)
            if self.cover_douyin_portrait.samefile(self.cover_douyin_landscape):
                raise MetadataError("抖音横、竖封面必须是两个独立文件")
        if self.do_bilibili:
            if len(self.title) > 80:
                raise MetadataError("B站标题最多 80 字，请编辑 publish.title")
            bilibili = publish["bilibili"]
            self.bilibili_tags = ",".join(validate_tags(bilibili["tags"], 11))
            self.bilibili_desc = story["zh"]["synopsis"]
            if len(self.bilibili_desc) > 2000:
                raise MetadataError("B站简介最多 2000 字，请编辑 zh.synopsis；不会静默截断")
            self.cover_bilibili = required_asset(self.path, story.get("image_file"), COVER_EXTS)
            self.tid = args.tid or bilibili["tid"]
            if not str(self.tid).isdigit() or int(self.tid) <= 0:
                raise MetadataError("B站分区号 tid 必须是正整数")

    def build_commands(self, headed: bool) -> list[tuple[str, list[str]]]:
        commands = []
        for platform, selected, account, desc, tags in (
            ("douyin", self.do_douyin, self.douyin_account, self.douyin_desc, self.douyin_tags),
            ("bilibili", self.do_bilibili, self.bilibili_account, self.bilibili_desc, self.bilibili_tags),
        ):
            if not selected:
                continue
            cmd = [
                str(sau_exe()),
                platform,
                "upload-video",
                "--account",
                account,
                "--file",
                str(self.video),
                "--title",
                self.title,
                "--desc",
                desc,
                "--tags",
                tags,
            ]
            if platform == "douyin":
                cmd += [
                    "--declaration",
                    DEFAULT_DOUYIN_DECLARATION,
                    "--collection",
                    DEFAULT_DOUYIN_COLLECTION,
                    "--thumbnail-portrait",
                    str(self.cover_douyin_portrait),
                    "--thumbnail-landscape",
                    str(self.cover_douyin_landscape),
                    "--headed" if headed else "--headless",
                ]
            else:
                cmd += ["--tid", str(self.tid), "--thumbnail", str(self.cover_bilibili)]
            if self.schedule:
                cmd += ["--schedule", self.schedule]
            commands.append(("抖音" if platform == "douyin" else "B站", cmd))
        return commands


def _stdin_is_interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def choose_target(value: str | None, ask: bool = False) -> Path:
    """metadata.json to publish, asking only when there is nothing usable saved.

    Same rule as 06_correct_subtitles: the materials launcher refreshes
    ``logs/.last_doraemon_materials`` on every run, so a weekly publish must not
    stop at a path prompt — a stray Enter there can send the previous episode.
    The story choice (when metadata has several stories) is still asked.
    """
    if value:
        return metadata_path(Path(value))
    state = REPO_ROOT / "logs/.last_doraemon_materials"
    default = state.read_text(encoding="utf-8-sig").strip() if state.is_file() else ""
    saved = metadata_path(Path(default)) if default else None
    if not ask and saved is not None and saved.is_file():
        print(f"素材目录：{saved.parent}")
        return saved
    if saved is not None and not saved.is_file():
        print(f"[!] logs/.last_doraemon_materials 记录的目录已不存在：{default}")
    if not ask and not _stdin_is_interactive():
        raise MetadataError("请指定素材目录或 metadata.json（没有可用记录；--ask 可强制询问）")
    if default:
        print(f"默认素材目录：{default}")
    entered = input("素材目录或 metadata.json（回车使用默认）: ").strip().strip('"')
    value = entered or default
    if not value:
        raise MetadataError("请指定素材目录或 metadata.json")
    return metadata_path(Path(value))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="读取 metadata.json，发布一个故事到抖音/B站")
    parser.add_argument("dir", nargs="?", help="素材目录或 metadata.json；默认使用上次素材目录")
    parser.add_argument("--story", help="故事标识，如 941_1")
    parser.add_argument("--only", choices=["douyin", "bilibili"], help="只发一个平台")
    parser.add_argument("--tid", help="覆盖 metadata 中的 B站分区号")
    parser.add_argument("--schedule", help='定时发布 "YYYY-MM-DD HH:MM"')
    parser.add_argument("--douyin-account", default="default")
    parser.add_argument("--bilibili-account", default="default")
    parser.add_argument("--headed", action="store_true", help="显示抖音浏览器")
    parser.add_argument("--yes", action="store_true", help="明确跳过发布确认")
    parser.add_argument("--dry-run", action="store_true", help="校验并打印命令，不发布")
    parser.add_argument(
        "--ask",
        "--prompt-target",
        dest="ask",
        action="store_true",
        help="强制询问素材目录 (默认直接用 logs/.last_doraemon_materials 里记录的目录)",
    )
    args = parser.parse_args()
    try:
        path = choose_target(args.dir, args.ask)
        data = load_metadata(path)
        if not args.story and len(data["stories"]) > 1:
            if args.yes:
                raise MetadataError("自动发布必须用 --story 明确选择故事")
            for story in data["stories"]:
                print(f"  {story['story_id']}：{story['publish']['title'] or story['title']}")
            args.story = input("选择故事标识（例如 941_1）: ").strip()
        job = PublishJob(path, args)
        commands = job.build_commands(args.headed)
    except (MetadataError, OSError, UnicodeError, EOFError) as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return 2

    print("=" * 50)
    print(f"  metadata : {job.path}")
    print(f"  故事     : {job.story_id}")
    print(f"  视频     : {job.video}")
    print(f"  标题     : {job.title}")
    if job.do_douyin:
        print(f"  抖音标签 : {job.douyin_desc}")
        print(f"  抖音封面 : {job.cover_douyin_portrait} / {job.cover_douyin_landscape}")
        print(f"  抖音声明 : {DEFAULT_DOUYIN_DECLARATION}；合集：{DEFAULT_DOUYIN_COLLECTION}")
    if job.do_bilibili:
        print(f"  B站标签  : {job.bilibili_tags}")
        print(f"  B站封面  : {job.cover_bilibili}")
        print(f"  B站简介  :\n{job.bilibili_desc}")
    print("=" * 50)
    if args.dry_run:
        for name, cmd in commands:
            print(f"[{name}] {subprocess.list2cmdline(cmd)}")
        return 0
    if not sau_exe().is_file():
        print("[X] 未安装 sau CLI，请先运行 bat\\setup_publish.bat")
        return 1
    if not args.yes:
        try:
            confirmed = input("确认发布？输入 Y 继续，其他键取消: ").strip().lower() == "y"
        except EOFError:
            confirmed = False
        if not confirmed:
            print("已取消。")
            return 1
    failed = 0
    for name, cmd in commands:
        try:
            proc = subprocess.run(cmd, cwd=SAU_ROOT, check=False)
            ok = proc.returncode == 0
        except OSError as exc:
            print(f"[{name}] 上传失败：{exc}")
            ok = False
        print(f"[{name}] {'成功' if ok else '失败'}")
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
