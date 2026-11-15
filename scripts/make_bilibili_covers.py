"""Make upload-safe JPEG covers from the Doraemon 4K Bilibili artwork.

The ComfyUI ``<story>_4k.jpg`` files can contain PNG data despite their suffix;
those 12+ MiB payloads make Bilibili's cover endpoint return HTTP 413. Keep the
source untouched and write a real JPEG at the same dimensions for publication.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from creator_agent.doraemon.metadata import MetadataError, load_metadata  # noqa: E402
from scripts.publish_dual import b_videos, choose_target  # noqa: E402

MAX_COVER_BYTES = 5 * 1024 * 1024
JPEG_QUALITIES = (88, 82, 76, 70, 60)


def upload_cover_path(source: Path) -> Path:
    return source.with_name(f"{source.stem}_bilibili.jpg")


def make_bilibili_cover(source: Path) -> Path:
    """Convert ``source`` to a same-size JPEG below Bilibili's request limit."""
    if not source.is_file():
        raise MetadataError(f"B站4K封面不存在：{source}")
    target = upload_cover_path(source)
    temporary = target.with_suffix(".tmp.jpg")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            if image.mode != "RGB":
                image = image.convert("RGB")
            dimensions = image.size
            for quality in JPEG_QUALITIES:
                image.save(temporary, format="JPEG", quality=quality, optimize=True)
                if temporary.stat().st_size <= MAX_COVER_BYTES:
                    temporary.replace(target)
                    print(
                        f"  {source.name}: {source.stat().st_size / 1024 / 1024:.2f} MiB → "
                        f"{target.name}: {target.stat().st_size / 1024 / 1024:.2f} MiB "
                        f"({dimensions[0]}x{dimensions[1]}, JPEG quality={quality})"
                    )
                    return target
        raise MetadataError(f"4K封面转换后仍超过 5 MiB：{source}")
    except (OSError, UnidentifiedImageError) as exc:
        raise MetadataError(f"无法转换B站封面 {source}：{exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="把 _4k.jpg 转成同尺寸的B站上传JPEG，原图不变")
    parser.add_argument("dir", nargs="?", help="素材目录或 metadata.json；默认使用上次素材目录")
    parser.add_argument("--story", help="只处理故事标识，如 942_1")
    parser.add_argument("--ask", "--prompt-target", action="store_true", help="强制询问素材目录")
    # publish_bilibili.bat forwards its arguments to both steps. Consume the
    # publisher-only options here; --dry-run still prepares this local asset,
    # but the second step will not log in or upload anything.
    parser.add_argument("--dy", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--b", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--only", choices=["douyin", "bilibili"], help=argparse.SUPPRESS)
    parser.add_argument("--tid", help=argparse.SUPPRESS)
    parser.add_argument("--schedule", help=argparse.SUPPRESS)
    parser.add_argument("--douyin-account", help=argparse.SUPPRESS)
    parser.add_argument("--bilibili-account", help=argparse.SUPPRESS)
    parser.add_argument("--headed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        path = choose_target(args.dir, args.ask)
        data = load_metadata(path)
        videos = b_videos(path.parent, data["stories"])
        if args.story:
            videos = [(story_id, video) for story_id, video in videos if story_id == args.story]
        if not videos:
            named = f"{args.story} 的" if args.story else ""
            raise MetadataError(f"{path.parent} 下没有 {named}_b 视频：请先准备 <故事>_b.mp4")
        for story_id in dict.fromkeys(story_id for story_id, _ in videos):
            make_bilibili_cover(path.parent / f"{story_id}_4k.jpg")
    except (MetadataError, OSError, UnicodeError, EOFError) as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
