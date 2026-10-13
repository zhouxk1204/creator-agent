"""Dual-platform video publisher: Douyin + Bilibili via social-auto-upload (sau).

Reads a publish directory prepared by the user:

    publish/<name>/
        video.mp4          # required: exactly one video file
        title.txt          # required: first non-empty line = title
        desc.txt           # optional: whole file = description (multi-line OK)
        tags.txt           # optional: tags, comma / space / newline separated
        cover-douyin.jpg   # optional: 3:4 portrait cover for Douyin
        cover-bilibili.jpg # optional: 16:10 landscape cover for Bilibili
        cover.jpg          # optional: fallback cover for both platforms
        meta.txt           # optional: key=value lines, see META KEYS below

The canonical format spec (with AI-generation prompt) is docs/publish-package.md.

META KEYS (meta.txt):
    tid              Bilibili category id (required for bilibili unless --tid)
    schedule         "YYYY-MM-DD HH:MM" scheduled publish time (both platforms)
    douyin_account   sau douyin account name   (default: --douyin-account / "default")
    bilibili_account sau bilibili account name (default: --bilibili-account / "default")
    douyin           "0"/"no" -> skip douyin
    bilibili         "0"/"no" -> skip bilibili

Stdlib-only so any project Python can run it. The actual upload is done by the
`sau` CLI installed under tools/social-auto-upload/.venv (bat/setup_publish.bat).
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAU_ROOT = REPO_ROOT / "tools" / "social-auto-upload"
PUBLISH_ROOT = REPO_ROOT / "publish"

VIDEO_EXTS = {".mp4", ".mov", ".flv", ".mkv", ".avi"}
COVER_EXTS = {".jpg", ".jpeg", ".png"}


def sau_exe() -> Path:
    if sys.platform == "win32":
        return SAU_ROOT / ".venv" / "Scripts" / "sau.exe"
    return SAU_ROOT / ".venv" / "bin" / "sau"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def read_meta(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not path.exists():
        return meta
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        meta[key.strip().lower()] = value.strip()
    return meta


def truthy_off(value: str | None) -> bool:
    return value is not None and value.lower() in {"0", "no", "false", "off", "skip"}


def find_single(folder: Path, exts: set[str], what: str, required: bool) -> Path | None:
    found = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts)
    if not found:
        if required:
            raise SystemExit(f"[X] {folder} 里没有{what}（支持: {' '.join(sorted(exts))}）")
        return None
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        raise SystemExit(f"[X] {folder} 里有多个{what}，只保留一个: {names}")
    return found[0]


def normalize_tags(raw: str) -> str:
    for ch in ("，", "\n", "\t", " "):
        raw = raw.replace(ch, ",")
    return ",".join(t for t in (x.strip() for x in raw.split(",")) if t)


def resolve_covers(folder: Path) -> tuple[Path | None, Path | None]:
    """Resolve (douyin_cover, bilibili_cover) from the publish folder.

    Priority per platform: cover-<platform>.<ext> -> cover.<ext> -> the only
    image in the folder. Multiple un-named images are an error (ambiguous).
    """
    images = [p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in COVER_EXTS]

    def named(stem: str) -> Path | None:
        return next((p for p in images if p.stem.lower() == stem), None)

    generic = named("cover")
    douyin = named("cover-douyin") or generic
    bilibili = named("cover-bilibili") or generic
    if douyin is None and bilibili is None:
        unnamed = [p for p in images if not p.stem.lower().startswith("cover-")]
        if len(unnamed) == 1:
            douyin = bilibili = unnamed[0]
        elif len(unnamed) > 1:
            names = ", ".join(p.name for p in images)
            raise SystemExit(
                "[X] 有多张图片，无法判断封面：请命名为 "
                f"cover-douyin.jpg / cover-bilibili.jpg / cover.jpg（现有: {names}）"
            )
    return douyin, bilibili


class PublishJob:
    def __init__(self, folder: Path, args: argparse.Namespace):
        self.folder = folder
        self.video = find_single(folder, VIDEO_EXTS, "视频文件", required=True)
        self.cover_douyin, self.cover_bilibili = resolve_covers(folder)

        title_file = folder / "title.txt"
        if not title_file.exists():
            raise SystemExit(f"[X] 缺少 {title_file}（第一行作为标题）")
        self.title = next((ln.strip() for ln in title_file.read_text(encoding="utf-8").splitlines() if ln.strip()), "")
        if not self.title:
            raise SystemExit(f"[X] {title_file} 是空的")

        desc_file = folder / "desc.txt"
        self.desc = desc_file.read_text(encoding="utf-8").strip() if desc_file.exists() else ""

        tags_file = folder / "tags.txt"
        self.tags = normalize_tags(tags_file.read_text(encoding="utf-8")) if tags_file.exists() else ""

        meta = read_meta(folder / "meta.txt")
        self.schedule = args.schedule or meta.get("schedule") or None
        self.tid = args.tid or meta.get("tid") or None
        self.douyin_account = meta.get("douyin_account") or args.douyin_account
        self.bilibili_account = meta.get("bilibili_account") or args.bilibili_account

        only = args.only
        self.do_douyin = only in (None, "douyin") and not truthy_off(meta.get("douyin"))
        self.do_bilibili = only in (None, "bilibili") and not truthy_off(meta.get("bilibili"))
        if self.do_bilibili and not self.tid:
            raise SystemExit("[X] B站发布需要分区号 tid：在 meta.txt 里写 tid=xxx，或加命令行参数 --tid xxx")
        if not self.do_douyin and not self.do_bilibili:
            raise SystemExit("[X] 两个平台都被跳过了，无事可做")

    def build_commands(self, headed: bool) -> list[tuple[str, list[str]]]:
        sau = str(sau_exe())
        cmds: list[tuple[str, list[str]]] = []
        if self.do_douyin:
            cmd = [
                sau,
                "douyin",
                "upload-video",
                "--account",
                self.douyin_account,
                "--file",
                str(self.video),
                "--title",
                self.title,
            ]
            if self.desc:
                cmd += ["--desc", self.desc]
            if self.tags:
                cmd += ["--tags", self.tags]
            if self.cover_douyin:
                cmd += ["--thumbnail", str(self.cover_douyin)]
            if self.schedule:
                cmd += ["--schedule", self.schedule]
            cmd += ["--headed" if headed else "--headless"]
            cmds.append(("抖音", cmd))
        if self.do_bilibili:
            cmd = [
                sau,
                "bilibili",
                "upload-video",
                "--account",
                self.bilibili_account,
                "--file",
                str(self.video),
                "--title",
                self.title,
                "--desc",
                self.desc or self.title,
                "--tid",
                str(self.tid),
            ]
            if self.tags:
                cmd += ["--tags", self.tags]
            if self.cover_bilibili:
                cmd += ["--thumbnail", str(self.cover_bilibili)]
            if self.schedule:
                cmd += ["--schedule", self.schedule]
            cmds.append(("B站", cmd))
        return cmds


def find_candidates() -> list[Path]:
    if not PUBLISH_ROOT.exists():
        return []
    out = []
    for sub in sorted(PUBLISH_ROOT.iterdir()):
        if sub.is_dir() and any(p.suffix.lower() in VIDEO_EXTS for p in sub.iterdir() if p.is_file()):
            out.append(sub)
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="一键发布视频到 抖音 + B站（经 social-auto-upload）")
    parser.add_argument(
        "dir", nargs="?", help="发布目录（如 publish/2026-10-13-xxx）。省略时自动找 publish/ 下唯一候选"
    )
    parser.add_argument("--only", choices=["douyin", "bilibili"], help="只发一个平台")
    parser.add_argument("--tid", help="B站分区号（覆盖 meta.txt）")
    parser.add_argument("--schedule", help='定时发布 "YYYY-MM-DD HH:MM"（覆盖 meta.txt）')
    parser.add_argument("--douyin-account", default="default", help="抖音账号名（默认 default）")
    parser.add_argument("--bilibili-account", default="default", help="B站账号名（默认 default）")
    parser.add_argument("--headed", action="store_true", help="抖音上传时显示浏览器窗口（调试用）")
    parser.add_argument("--yes", action="store_true", help="跳过发布前确认")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的 sau 命令，不真正发布")
    args = parser.parse_args()

    if not sau_exe().exists():
        print(f"[X] 未找到 sau CLI: {sau_exe()}")
        print("    请先运行一次性安装: bat\\setup_publish.bat")
        return 1

    folder = Path(args.dir).resolve() if args.dir else None
    if folder is None:
        candidates = find_candidates()
        if len(candidates) == 1:
            folder = candidates[0]
        elif not candidates:
            print(f"[X] {PUBLISH_ROOT} 下没有可发布的目录（需要含视频文件的子目录）")
            return 1
        else:
            print("[X] publish/ 下有多个候选目录，请指定一个：")
            for c in candidates:
                print(f"    {c.relative_to(REPO_ROOT)}")
            return 1
    if not folder.is_dir():
        print(f"[X] 目录不存在: {folder}")
        return 1

    job = PublishJob(folder, args)
    commands = job.build_commands(headed=args.headed)

    platforms = " + ".join(name for name, _ in commands)
    print("=" * 50)
    print(f"  发布目录 : {folder.relative_to(REPO_ROOT) if folder.is_relative_to(REPO_ROOT) else folder}")
    print(f"  视频     : {job.video.name}")
    print(f"  标题     : {job.title}")
    cover_d = job.cover_douyin.name if job.cover_douyin else "（无，自动截取）"
    cover_b = job.cover_bilibili.name if job.cover_bilibili else "（无，自动截取）"
    print(f"  抖音封面 : {cover_d}   B站封面: {cover_b}")
    print(f"  平台     : {platforms}")
    if job.schedule:
        print(f"  定时     : {job.schedule}")
    print("=" * 50)

    if args.dry_run:
        for name, cmd in commands:
            print(f"\n[{name}] 将执行:")
            print("  " + subprocess.list2cmdline(cmd))
        return 0

    if not args.yes:
        answer = input("确认发布？输入 Y 继续，其他键取消: ").strip().lower()
        if answer != "y":
            print("已取消。")
            return 1

    results: dict[str, bool] = {}
    for name, cmd in commands:
        print(f"\n>>> 开始发布到【{name}】...")
        # cwd=sau repo root: douyin SMS verification reads verify_code.txt from there.
        proc = subprocess.run(cmd, cwd=SAU_ROOT)
        results[name] = proc.returncode == 0
        print(f"<<< 【{name}】{'成功' if results[name] else f'失败 (exit {proc.returncode})'}")

    print("\n" + "=" * 50)
    for name, ok in results.items():
        print(f"  {name}: {'✓ 成功' if ok else '✗ 失败'}")
    print("=" * 50)
    if all(results.values()):
        print("全部发布完成！")
        return 0
    print("有平台发布失败——失败的可以稍后单独重跑（--only douyin / --only bilibili），成功的不受影响。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
