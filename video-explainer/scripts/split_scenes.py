#!/usr/bin/env python3
"""命令行入口：视频 → 分镜检测 → FFmpeg 切割 → scenes.json。

用法:
    python scripts/split_scenes.py "D:\\videos\\935#1.mp4"
    python scripts/split_scenes.py input.mp4 --output out_dir --threshold 25 \
        --min-scene-len 8 --no-export --overwrite

退出码: 0 = 全部成功；1 = 流程错误；2 = 有分镜导出失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from models import DetectorConfig  # noqa: E402
from scene_splitter import SceneSplitError, SceneSplitter  # noqa: E402

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scene_config.json"


def load_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[ERROR] 配置文件不是合法 JSON: {path}\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    return data.get("detector", {})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="视频分镜检测与切割（第一阶段：视频 → 分镜 → scenes.json）")
    parser.add_argument("input", type=Path, help="输入视频路径")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="输出目录（默认: 与输入视频同名、去掉扩展名的目录）",
    )
    parser.add_argument("--threshold", type=float, default=None, help="ContentDetector 阈值")
    parser.add_argument("--min-scene-len", type=int, default=None, help="最短分镜长度（帧）")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"配置文件路径（默认: {DEFAULT_CONFIG_PATH}）",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="只检测分镜、生成 scenes.json，不切割导出视频",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="输出目录已存在时允许覆盖（默认拒绝）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    file_cfg = load_config(args.config)
    config = DetectorConfig(
        type=file_cfg.get("type", "content"),
        threshold=args.threshold if args.threshold is not None else file_cfg.get("threshold", 27.0),
        min_scene_len_frames=args.min_scene_len
        if args.min_scene_len is not None
        else file_cfg.get("min_scene_len_frames", 12),
    )

    splitter = SceneSplitter(
        input_video=args.input,
        output_dir=args.output,
        config=config,
        export=not args.no_export,
        overwrite=args.overwrite,
    )
    try:
        summary = splitter.run()
    except SceneSplitError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    return 0 if summary.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
