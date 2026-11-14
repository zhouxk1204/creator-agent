"""Correct and retranslate JianYing bilingual Doraemon subtitles with the local LLM."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from translate_doraemon import translate_metadata

from creator_agent.config import load_settings
from creator_agent.doraemon.metadata import MetadataError, load_metadata, story_for_subtitle
from creator_agent.translate.corrector import SubtitleCorrectionError, SubtitleCorrectionPipeline
from creator_agent.translate.server import ensure_server, stop_server

REPO_ROOT = Path(__file__).resolve().parent.parent


def default_target() -> Path:
    materials_state = REPO_ROOT / "logs" / ".last_doraemon_materials"
    if materials_state.is_file():
        try:
            folder = materials_state.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError) as exc:
            raise SubtitleCorrectionError(f"无法读取 logs/.last_doraemon_materials：{exc}") from exc
        if not folder:
            raise SubtitleCorrectionError("logs/.last_doraemon_materials 内容为空，请手动指定素材目录")
        return Path(folder)

    state = REPO_ROOT / "logs" / ".last_fetched"
    if not state.is_file():
        raise SubtitleCorrectionError("未指定目录，且 logs/.last_doraemon_materials / logs/.last_fetched 均不存在")
    episode = state.read_text(encoding="utf-8").strip()
    if not episode.isdigit():
        raise SubtitleCorrectionError("logs/.last_fetched 内容无效")
    return Path.home() / "Desktop" / f"doraemon_{int(episode)}"


def _stdin_is_interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def resolve_target(ask: bool = False) -> Path:
    """The folder to process, asking only when there is nothing usable saved.

    The materials launcher writes ``logs/.last_doraemon_materials`` on every run,
    so a weekly ``06`` must start straight away — pressing Enter every time is
    pure friction, and a wrong Enter silently processes the previous episode.
    ``--ask`` (alias ``--prompt-target``) still offers the choice explicitly.
    """
    if ask:
        return prompt_target()
    try:
        target = default_target()
    except SubtitleCorrectionError as exc:
        if not _stdin_is_interactive():
            raise
        print(f"  No usable saved folder: {exc}")
        return prompt_target()
    if not target.exists():
        # The record outlived the folder (deleted / moved / different machine).
        # Fall back to asking instead of failing on a path the user cannot fix.
        if not _stdin_is_interactive():
            raise SubtitleCorrectionError(f"记录的素材目录已不存在：{target}")
        print(f"  Saved folder is gone: {target}")
        return prompt_target()
    print(f'  Materials folder: "{target}"')
    print("  (logs/.last_doraemon_materials — --ask 可改用其它目录)")
    return target


def prompt_target() -> Path:
    default = None
    default_error = None
    try:
        default = default_target()
    except SubtitleCorrectionError as exc:
        default_error = exc

    if default is not None:
        print(f'  Saved materials folder: "{default}"')
        prompt = "Materials folder (Enter = saved default; paste another path to override): "
    else:
        print(f"  No usable saved folder: {default_error}")
        prompt = "Materials folder (paste a path; Enter = fail): "

    entered = input(prompt).strip()
    if len(entered) >= 2 and entered.startswith('"') and entered.endswith('"'):
        entered = entered[1:-1].strip()
    if entered:
        return Path(entered)
    if default is not None:
        return default
    if default_error is not None:
        raise default_error
    raise SubtitleCorrectionError("未指定素材目录")


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
    parser = argparse.ArgumentParser(description="先翻译标题/简介并生成标签，再矫正剪映双语字幕并重新翻译为中文")
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="包含 *_origin.srt 和 metadata.json 的目录，或单个 *_origin.srt；默认使用上次素材下载记录的目录",
    )
    parser.add_argument("--force", action="store_true", help="明确覆盖已有 *_jp_fixed.srt / *_zh_fixed.srt")
    parser.add_argument("--refresh-translation", action="store_true", help="重新翻译 metadata 中文简介并重建两平台标签")
    parser.add_argument(
        "--ask",
        "--prompt-target",
        dest="ask",
        action="store_true",
        help="强制询问素材目录 (默认直接用 logs/.last_doraemon_materials 里记录的目录)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        target = args.target
        if target is None:
            target = resolve_target(args.ask)
        inputs = discover_inputs(target)
        load_metadata(inputs[0].parent / "metadata.json")
    except (SubtitleCorrectionError, MetadataError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    settings = load_settings().translate
    server = None
    try:
        server = ensure_server(settings.base_url)
        pipeline = SubtitleCorrectionPipeline(settings)
        failed = 0
        skipped: list[str] = []
        for position, origin in enumerate(inputs, 1):
            print(f"\n===== {position}/{len(inputs)} {origin.name} =====")
            try:
                base = origin.name.removesuffix("_origin.srt")
                # Rerunning a launcher after a later stage failed must not fail on
                # the files it already produced: skip them (no overwrite, so a
                # hand-edited *_fixed.srt stays safe) and still exit 0.
                done = not args.force and any(
                    origin.with_name(f"{base}_{suffix}_fixed.srt").exists() for suffix in ("jp", "zh")
                )
                if done and not args.refresh_translation:
                    print(f"[跳过] 字幕已存在：{base}_jp_fixed.srt / {base}_zh_fixed.srt (--force 覆盖)")
                    skipped.append(origin.name)
                    continue
                path = origin.parent / "metadata.json"
                story = story_for_subtitle(load_metadata(path), origin)
                print(f"[简介] 翻译 {story['story_id']} 并保存 metadata 平台标签...")
                _, _, tags_ok = translate_metadata(path, story["story_id"], settings, refresh=args.refresh_translation)
                if not tags_ok:
                    print("[警告] 发布标签生成失败，继续字幕处理；本次运行将返回失败状态", file=sys.stderr)
                if done:  # --refresh-translation only asked for the metadata stage
                    print(f"[跳过] 字幕已存在，仅刷新了简介/标签：{base} (--force 覆盖字幕)")
                    skipped.append(origin.name)
                    continue
                result = pipeline.process_file(origin, force=args.force)
                if not tags_ok:
                    failed += 1
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
        if skipped:
            print(f"\n[跳过] {len(skipped)} 个已有结果：{', '.join(skipped)} (需要重做请加 --force)")
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
