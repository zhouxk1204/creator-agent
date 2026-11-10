"""Correct and retranslate JianYing bilingual Doraemon subtitles with the local LLM."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from creator_agent.config import load_settings
from creator_agent.translate.corrector import SubtitleCorrectionError, SubtitleCorrectionPipeline
from creator_agent.translate.server import ensure_server, stop_server

REPO_ROOT = Path(__file__).resolve().parent.parent


def default_target() -> Path:
    state = REPO_ROOT / "logs" / ".last_fetched"
    if not state.is_file():
        raise SubtitleCorrectionError("未指定目录，且 logs/.last_fetched 不存在")
    episode = state.read_text(encoding="utf-8").strip()
    if not episode.isdigit():
        raise SubtitleCorrectionError("logs/.last_fetched 内容无效")
    return Path.home() / "Desktop" / f"doraemon_{int(episode)}"


def discover_inputs(target: Path) -> list[Path]:
    target = target.expanduser().resolve()
    if target.is_file():
        if not target.name.endswith("_origin.srt"):
            raise SubtitleCorrectionError(f"输入文件必须以 _origin.srt 结尾：{target}")
        return [target]
    if not target.is_dir():
        raise SubtitleCorrectionError(f"目录不存在：{target}")
    inputs = sorted(target.glob("*_origin.srt"))
    if not inputs:
        raise SubtitleCorrectionError(f"目录中没有 *_origin.srt：{target}")
    return inputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用本地模型矫正剪映日文 ASR 双语字幕，再重新翻译为中文")
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="包含 *_origin.srt 和同名 *_jp.md 的目录，或单个 *_origin.srt；默认使用最新话桌面目录",
    )
    parser.add_argument("--force", action="store_true", help="明确覆盖已有 *_jp_fixed.srt / *_zh_fixed.srt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        target = args.target or default_target()
        inputs = discover_inputs(target)
    except SubtitleCorrectionError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    settings = load_settings().translate
    server = None
    try:
        server = ensure_server(settings.base_url)
        pipeline = SubtitleCorrectionPipeline(settings)
        failed = 0
        for position, origin in enumerate(inputs, 1):
            print(f"\n===== {position}/{len(inputs)} {origin.name} =====")
            try:
                result = pipeline.process_file(origin, force=args.force)
            except Exception as exc:
                failed += 1
                print(f"[失败] {origin.name}：{exc}", file=sys.stderr)
                continue
            print(
                f"[完成] {result.jp_fixed.name} / {result.zh_fixed.name} "
                f"（原始日文 {result.source_cues} 条 → 修正后 {result.fixed_cues} 条）"
            )
            if result.questions:
                print(f"[存疑] {result.questions.name}")
        return 1 if failed else 0
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            print("[server] stopping translation LLM...")
            stop_server(server)


if __name__ == "__main__":
    raise SystemExit(main())
