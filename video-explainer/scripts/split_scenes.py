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

from models import DetectorConfig, PostProcessConfig  # noqa: E402
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
    return data


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
    # ---- 后处理：闪烁误切合并 + 黑场标记 ----
    parser.add_argument(
        "--merge-threshold",
        type=float,
        default=None,
        help="边界复核合并阈值：min_cross 低于该值的边界被撤销（默认 100）",
    )
    parser.add_argument(
        "--cross-window",
        type=int,
        default=None,
        help="边界两侧各检查的帧数（默认 6）",
    )
    parser.add_argument(
        "--analysis-scale",
        type=float,
        default=None,
        help="后处理分析用缩放比例（默认 0.5）",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="关闭闪烁误切合并",
    )
    parser.add_argument(
        "--no-black-mark",
        action="store_true",
        help="关闭黑场标记",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只生成 postprocess_report.json 分析报告，分镜结果保持检测原样",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    file_cfg = load_config(args.config)
    det_cfg = file_cfg.get("detector", {})
    pp_cfg = file_cfg.get("postprocess", {})
    config = DetectorConfig(
        type=det_cfg.get("type", "content"),
        threshold=args.threshold if args.threshold is not None else det_cfg.get("threshold", 27.0),
        min_scene_len_frames=args.min_scene_len
        if args.min_scene_len is not None
        else det_cfg.get("min_scene_len_frames", 12),
    )
    postprocess = PostProcessConfig(
        merge_enabled=pp_cfg.get("merge_enabled", True) and not args.no_merge,
        merge_threshold=args.merge_threshold
        if args.merge_threshold is not None
        else pp_cfg.get("merge_threshold", 100.0),
        cross_window=args.cross_window if args.cross_window is not None else pp_cfg.get("cross_window", 6),
        analysis_scale=args.analysis_scale if args.analysis_scale is not None else pp_cfg.get("analysis_scale", 0.5),
        merge_max_iterations=pp_cfg.get("merge_max_iterations", 4),
        black_enabled=pp_cfg.get("black_enabled", True) and not args.no_black_mark,
        black_max_yavg=pp_cfg.get("black_max_yavg", 20.0),
        black_max_ystd=pp_cfg.get("black_max_ystd", 8.0),
        black_min_pblack=pp_cfg.get("black_min_pblack", 0.95),
        black_pixel_threshold=pp_cfg.get("black_pixel_threshold", 32),
        black_sample_frames=pp_cfg.get("black_sample_frames", 8),
        black_min_frame_ratio=pp_cfg.get("black_min_frame_ratio", 0.95),
        dry_run=args.dry_run or pp_cfg.get("dry_run", False),
    )

    splitter = SceneSplitter(
        input_video=args.input,
        output_dir=args.output,
        config=config,
        export=not args.no_export,
        overwrite=args.overwrite,
        postprocess=postprocess,
    )
    try:
        summary = splitter.run()
    except SceneSplitError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    return 0 if summary.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
