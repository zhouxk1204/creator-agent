"""Publish one Doraemon metadata story to Douyin/Bilibili via social-auto-upload.

Douyin takes the 剪映 export: every ``<materials>/<story>_dy/<name>_dy.mp4`` is a
finished vertical cut with burned subtitles, and ``--dy`` publishes each of them
in turn (one Douyin video per file). The dual-platform flow keeps using
``publish.video_path`` for Bilibili; ``--b`` instead publishes every root-level
``<story>_b`` video with an optimized JPEG made from its ``<story>_4k.jpg`` cover.

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
# Douyin shows 5 topics per video; more are ignored by the UI or rejected.
DOUYIN_TAG_LIMIT = 5


def sau_exe() -> Path:
    return SAU_ROOT / ".venv" / ("Scripts/sau.exe" if sys.platform == "win32" else "bin/sau")


def required_asset(path: Path, value, extensions: set[str]) -> Path:
    asset = resolve_asset(path, value)
    if not asset.is_file() or asset.suffix.lower() not in extensions:
        raise MetadataError(f"素材不存在或格式不支持：{asset}")
    return asset


def dy_videos(materials: Path, stories: list[dict]) -> list[tuple[str, Path]]:
    """``(story_id, video)`` for every 剪映 ``_dy`` export under the materials folder.

    The convention is ``<materials>/<story_id>_dy/<story_id>_dy.mp4``: the folder
    holds the vertical cut with burned subtitles, exported from 剪映 after the
    subtitle stage. A story may export more than one file (a split upload), and
    not every story has been exported yet, so discovery — not a fixed path — is
    what drives the loop.
    """
    known = {story["story_id"] for story in stories}
    found: list[tuple[str, Path]] = []
    for folder in sorted(materials.glob("*_dy")):
        if not folder.is_dir():
            continue
        story_id = folder.name[: -len("_dy")]
        if story_id not in known:
            print(f"[!] 跳过 {folder.name}：metadata 里没有故事 {story_id}")
            continue
        videos = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTS)
        named = [path for path in videos if path.stem.endswith("_dy")]
        for video in named or videos:
            found.append((story_id, video))
    return found


def b_videos(materials: Path, stories: list[dict]) -> list[tuple[str, Path]]:
    """``(story_id, video)`` for every Bilibili ``<story_id>_b`` export."""
    known = {story["story_id"] for story in stories}
    found: list[tuple[str, Path]] = []
    for video in sorted(materials.glob("*_b.*")):
        if not video.is_file() or video.suffix.lower() not in VIDEO_EXTS:
            continue
        story_id = video.stem[: -len("_b")]
        if story_id not in known:
            print(f"[!] 跳过 {video.name}：metadata 里没有故事 {story_id}")
            continue
        found.append((story_id, video))
    return found


def douyin_video(metadata: Path, story: dict, override: Path | None = None) -> Path:
    """The file Douyin uploads: explicit override, then the ``_dy`` export.

    ``publish.video_path`` is the Bilibili master and is allowed to be missing
    for Douyin, because the 剪映 export in ``<story>_dy/`` is what actually gets
    uploaded there.
    """
    if override is not None:
        if not override.is_file():
            raise MetadataError(f"抖音视频不存在：{override}")
        return override
    story_id = story["story_id"]
    explicit = (story.get("publish", {}).get("douyin") or {}).get("video_path")
    if explicit:
        return required_asset(metadata, explicit, VIDEO_EXTS)
    exported = metadata.parent / f"{story_id}_dy" / f"{story_id}_dy.mp4"
    if exported.is_file():
        return exported
    master = (story.get("publish") or {}).get("video_path")
    if master and resolve_asset(metadata, master).is_file():
        return required_asset(metadata, master, VIDEO_EXTS)
    raise MetadataError(
        f"{story_id} 没有抖音视频：请把剪映导出的 {story_id}_dy/{story_id}_dy.mp4 放到素材目录，"
        "或在 metadata 的 publish.douyin.video_path 指定"
    )


class PublishJob:
    def __init__(
        self,
        target: Path,
        args: argparse.Namespace,
        story_id: str | None = None,
        video: Path | None = None,
    ):
        self.path = metadata_path(target)
        data = load_metadata(self.path)
        story_id = story_id or args.story
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
        self.do_douyin = args.only in (None, "douyin")
        self.do_bilibili = args.only in (None, "bilibili")
        self.bilibili_batch = getattr(args, "b", False)
        self.video_douyin = douyin_video(self.path, story, video) if self.do_douyin else None
        if self.do_bilibili:
            bilibili_source = (
                (str(video) if video is not None else f"{story_id}_b.mp4")
                if self.bilibili_batch
                else publish["video_path"]
            )
            self.video_bilibili = required_asset(self.path, bilibili_source, VIDEO_EXTS)
        else:
            self.video_bilibili = None
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
            # Douyin accepts at most 5 topics; the metadata writer already caps
            # them, but a hand-edited or older metadata.json must not push more
            # — trim instead of refusing to publish the video.
            raw_tags = douyin["tags"]
            if not isinstance(raw_tags, list):
                raise MetadataError("抖音标签必须是字符串数组")
            if len(raw_tags) > DOUYIN_TAG_LIMIT:
                kept = ", ".join(str(tag) for tag in raw_tags[:DOUYIN_TAG_LIMIT])
                print(
                    f"[!] 抖音最多 {DOUYIN_TAG_LIMIT} 个标签，本次只取前 {DOUYIN_TAG_LIMIT} 个：{kept}",
                    file=sys.stderr,
                )
            tags = validate_tags(list(raw_tags[:DOUYIN_TAG_LIMIT]), DOUYIN_TAG_LIMIT)
            self.douyin_tags = ",".join(tags)
            self.douyin_desc = " ".join(f"#{tag}" for tag in tags)
            if not douyin.get("cover_portrait_path") or not douyin.get("cover_landscape_path"):
                raise MetadataError(
                    f"{story_id} 的抖音横竖封面还没生成：先跑 scripts/make_douyin_covers.py "
                    "（publish_douyin.bat 的第一步会自动做）"
                )
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
            cover = f"{story_id}_4k_bilibili.jpg" if self.bilibili_batch else story.get("image_file")
            self.cover_bilibili = required_asset(self.path, cover, COVER_EXTS)
            self.tid = args.tid or bilibili["tid"]
            if not str(self.tid).isdigit() or int(self.tid) <= 0:
                raise MetadataError("B站分区号 tid 必须是正整数")

    @property
    def video(self) -> Path | None:
        """The file this job uploads first (Douyin, else Bilibili)."""
        return self.video_douyin or self.video_bilibili

    def build_commands(self, headed: bool) -> list[tuple[str, list[str]]]:
        commands = []
        for platform, selected, account, desc, tags, video in (
            ("douyin", self.do_douyin, self.douyin_account, self.douyin_desc, self.douyin_tags, self.video_douyin),
            (
                "bilibili",
                self.do_bilibili,
                self.bilibili_account,
                self.bilibili_desc,
                self.bilibili_tags,
                self.video_bilibili,
            ),
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
                str(video),
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


def login_command(platform: str, account: str) -> list[str]:
    """The sign-in command sau asks for in its cookie error message.

    Douyin's login needs a visible browser (QR code), Bilibili's does not take
    a --headed flag at all.
    """
    cmd = [str(sau_exe()), platform, "login", "--account", account]
    if platform == "douyin":
        cmd.append("--headed")
    return cmd


def check_session(platform: str, account: str) -> tuple[bool, str]:
    """``(usable, detail)`` for an account's saved session.

    ``sau <platform> check`` covers both "cookie file missing" and "cookie
    expired" (it validates the session headless), so no separate file probing is
    needed and the answer matches what the uploader will do.
    """
    check = subprocess.run(
        [str(sau_exe()), platform, "check", "--account", account],
        cwd=SAU_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    detail = " ".join(((check.stdout or "") + (check.stderr or "")).split())[-200:]
    return check.returncode == 0, detail


def ensure_session(platform: str, account: str) -> bool:
    """Make sure ``account`` can upload, signing in when its cookie is gone.

    The fix sau's own error message tells the user to run by hand —
    ``sau <platform> login`` — happens here instead of failing the whole publish
    after the videos were prepared. Only possible when a human can scan the QR
    code; otherwise the error names the exact command to run.
    """
    name = "抖音" if platform == "douyin" else "B站"
    usable, detail = check_session(platform, account)
    if usable:
        return True
    if not _stdin_is_interactive():
        raise MetadataError(
            f"{name} 账号 {account} 未登录或登录已过期，且当前没有终端可以扫码；"
            f"请先运行 sau {platform} login --account {account}"
        )
    print(f"[登录] {name} 账号 {account} 未登录/已过期（{detail or 'check failed'}），正在打开登录窗口，请扫码…")
    login = subprocess.run(login_command(platform, account), cwd=SAU_ROOT, check=False)
    if login.returncode != 0:
        raise MetadataError(f"{name} 登录没有完成，请手动运行 sau {platform} login --account {account}")
    print(f"[登录] {name} 完成")
    return True


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
    batch = parser.add_mutually_exclusive_group()
    batch.add_argument(
        "--dy",
        action="store_true",
        help="发布素材目录下所有 <故事>_dy/ 里的剪映导出（一个视频一条抖音），逐个循环",
    )
    batch.add_argument(
        "--b",
        action="store_true",
        help="发布素材目录下所有 <故事>_b 视频到B站，封面用对应4K图的优化JPEG",
    )
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
        if args.b and args.only == "douyin":
            raise MetadataError("--b 只发布B站，不能和 --only douyin 一起使用")
        if args.b:
            args.only = "bilibili"
        if args.dy or args.b:
            kind = "_b" if args.b else "_dy"
            videos = b_videos(path.parent, data["stories"]) if args.b else dy_videos(path.parent, data["stories"])
            if args.story:
                videos = [(story_id, video) for story_id, video in videos if story_id == args.story]
            if not videos:
                named = f"{args.story} 的" if args.story else ""
                expected = "<故事>_b.mp4" if args.b else "<故事>_dy/<故事>_dy.mp4"
                raise MetadataError(f"{path.parent} 下没有 {named}{kind} 视频：请先准备 {expected}")
            by_story: dict[str, int] = {}
            for story_id, _ in videos:
                by_story[story_id] = by_story.get(story_id, 0) + 1
            for story_id, count in by_story.items():
                if count > 1:
                    print(
                        f"[!] {story_id} 有 {count} 个 {kind} 视频，将用同一个标题/简介/封面各发一条", file=sys.stderr
                    )
            jobs = [PublishJob(path, args, story_id=story_id, video=video) for story_id, video in videos]
        else:
            if not args.story and len(data["stories"]) > 1:
                if args.yes:
                    raise MetadataError("自动发布必须用 --story 明确选择故事")
                for story in data["stories"]:
                    print(f"  {story['story_id']}：{story['publish']['title'] or story['title']}")
                args.story = input("选择故事标识（例如 941_1）: ").strip()
            jobs = [PublishJob(path, args)]
        plan = [(job, job.build_commands(args.headed)) for job in jobs]
    except (MetadataError, OSError, UnicodeError, EOFError) as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return 2

    for job, _ in plan:
        print("=" * 50)
        print(f"  metadata : {job.path}")
        print(f"  故事     : {job.story_id}")
        if job.do_douyin:
            print(f"  抖音视频 : {job.video_douyin}")
        if job.do_bilibili:
            print(f"  B站视频  : {job.video_bilibili}")
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
    if len(plan) > 1:
        print(f"  共 {len(plan)} 条待发布")
    if args.dry_run:
        for _, commands in plan:
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
    # Log in before uploading, not after the first upload already failed: a
    # missing/expired cookie is a two-second `check`, and the login it asks for
    # is exactly what we can run here.
    sessions: list[tuple[str, str]] = []
    for job, _ in plan:
        if job.do_douyin:
            sessions.append(("douyin", job.douyin_account))
        if job.do_bilibili:
            sessions.append(("bilibili", job.bilibili_account))
    try:
        for platform, account in dict.fromkeys(sessions):
            ensure_session(platform, account)
    except MetadataError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return 1
    failed = 0
    for job, commands in plan:
        if len(plan) > 1:
            print(f"\n--- {job.story_id}: {job.video_douyin or job.video_bilibili} ---")
        for name, cmd in commands:
            platform = "douyin" if cmd[1] == "douyin" else "bilibili"
            account = job.douyin_account if platform == "douyin" else job.bilibili_account
            try:
                ok = subprocess.run(cmd, cwd=SAU_ROOT, check=False).returncode == 0
            except OSError as exc:
                print(f"[{name}] 上传失败：{exc}")
                ok = False
            if not ok:
                # A cookie that expired between the pre-flight check and the
                # upload looks like any other failure; only a session that is
                # now invalid is worth a login + one retry.
                usable, _ = check_session(platform, account)
                if not usable:
                    try:
                        print(f"[{name}] 上传失败且登录已失效，登录后重试一次…")
                        ensure_session(platform, account)
                    except MetadataError as exc:
                        print(f"[{name}] 自动登录失败：{exc}", file=sys.stderr)
                    else:
                        try:
                            ok = subprocess.run(cmd, cwd=SAU_ROOT, check=False).returncode == 0
                        except OSError as exc:
                            print(f"[{name}] 重试失败：{exc}")
                            ok = False
            print(f"[{name}] {'成功' if ok else '失败'}")
            failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
