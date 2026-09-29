"""Vocal-separated Japanese ASR for local video files (Qwen3-ASR).

Offline: no browser, no pipeline - isolates vocals (audio-separator),
transcribes with Qwen3-ASR in the dedicated ``creator-asr-ja`` env, and
writes per video:

    <name>.transcript.json   segments + metadata
    <name>.txt               one segment per line
    <name>.srt               Japanese subtitles

Usage:
    uv run python scripts/transcribe_ja.py                 # process ja_asr.input_dir
    uv run python scripts/transcribe_ja.py video.mp4 [more.mp4 ...]
    uv run python scripts/transcribe_ja.py "storage/**/*.mp4" --out-dir out/
    uv run python scripts/transcribe_ja.py --force         # redo already-transcribed
    uv run python scripts/transcribe_ja.py ep01.mp4 --device mps --model Qwen/Qwen3-ASR-0.6B-hf
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

# Make Japanese text print correctly on the Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from creator_agent.asr.ja_transcriber import JaTranscriber, scan_videos, split_pending
from creator_agent.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Vocal-separated Japanese ASR for local videos.")
    parser.add_argument("videos", nargs="*", help="video paths or globs (blank = scan ja_asr.input_dir)")
    parser.add_argument("--out-dir", "-o", default=None, help="output dir (default: next to each video)")
    parser.add_argument("--language", "-l", default=None, help="ASR language (default: config)")
    parser.add_argument("--device", default=None, help="cuda:0 / mps / cpu")
    parser.add_argument("--model", default=None, help="e.g. Qwen/Qwen3-ASR-0.6B-hf")
    parser.add_argument("--force", "-f", action="store_true", help="reprocess videos that already have a .srt")
    args = parser.parse_args()

    settings = load_settings()
    ja = settings.ja_asr
    overrides = {k: v for k, v in {"language": args.language, "device": args.device, "model": args.model}.items() if v}
    if overrides:
        ja = ja.model_copy(update=overrides)

    out = Path(args.out_dir) if args.out_dir else None
    if args.videos:
        paths: list[Path] = []
        for arg in args.videos:
            matches = [Path(m) for m in sorted(glob.glob(arg, recursive=True)) if Path(m).is_file()]
            if matches:
                paths.extend(matches)
            else:
                print(f"  ! not found: {arg}")
    else:
        input_dir = Path(ja.input_dir)
        input_dir.mkdir(parents=True, exist_ok=True)
        paths = scan_videos(input_dir)
        print(f"Scanning {input_dir} ...")
        if not paths:
            print(f"No videos found. Drop video files into {input_dir} and re-run.")
            return

    pending, skipped = (paths, []) if args.force else split_pending(paths, out)
    for p in skipped:
        print(f"  - skip (has .srt): {p.name}")
    if not pending:
        print(f"Nothing to do ({len(skipped)} already transcribed; --force to redo).")
        return

    # Only require the worker env once there is actual work to do.
    if not ja.env_python or not Path(ja.env_python).exists():
        print("ERROR: ja_asr.env_python not set in config/settings.yaml (see README: creator-asr-ja env).")
        sys.exit(2)

    print(f"Transcribing {len(pending)} video(s) with vocal separation + {ja.model} ...")
    transcriber = JaTranscriber(settings=ja)
    done, failed = transcriber.transcribe_files(pending, out)
    print(f"\nDone: {done} transcribed, {len(failed)} failed.")
    for name, err in failed:
        print(f"  {name}: {err}")
    sys.exit(1 if failed and done == 0 else 0)


if __name__ == "__main__":
    main()
