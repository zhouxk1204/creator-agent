"""Transcribe the latest downloaded videos for a creator (Phase 2 ASR).

Offline: no browser needed - works on videos already at ``VIDEO_DOWNLOADED``.
Uses the dedicated ``creator-asr`` conda env via the Transcriber subprocess.

Prints the transcript path + first line of text for each transcribed video.

Usage:
    uv run python scripts/transcribe_latest.py <creator_id>              # all pending
    uv run python scripts/transcribe_latest.py <creator_id> --limit 3    # newest 3
    uv run python scripts/transcribe_latest.py douyin_MS4w...  --limit 1
"""

from __future__ import annotations

import argparse
import sys

# Make Chinese text print correctly on the Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from creator_agent.asr.transcriber import Transcriber
from creator_agent.config import load_settings
from creator_agent.models.video import VideoStatus
from creator_agent.repository.sqlite_repo import Repository
from creator_agent.storage.file_storage import FileStorage


def main(creator_id: str, limit: int | None) -> None:
    settings = load_settings()
    if not settings.asr.env_python:
        print("ERROR: asr.env_python not set in config/settings.yaml")
        sys.exit(2)

    repo = Repository(settings.db_path)
    storage = FileStorage(settings.storage_dir)
    transcriber = Transcriber(storage=storage, settings=settings.asr, repo=repo)
    try:
        creator = repo.get_creator(creator_id)
        if not creator:
            print(f"Creator not found: {creator_id}")
            sys.exit(1)

        pending = [
            v
            for v in repo.list_videos_by_status([VideoStatus.VIDEO_DOWNLOADED])
            if v.creator_id == creator.id and not storage.load_transcript(creator, v)
        ]
        if limit is not None:
            pending = pending[:limit]
        if not pending:
            print(f"No pending ASR videos for {creator.nickname} ({creator.id}).")
            return

        print(f"Transcribing {len(pending)} video(s) for {creator.nickname} ({creator.id})...")
        transcribed, failed = transcriber.transcribe_batch(creator, pending)
        print(f"\nDone: {transcribed} transcribed, {len(failed)} failed.")
        for vid, err in failed:
            print(f"  FAIL {vid}: {err}")

        for video in pending:
            t = storage.load_transcript(creator, video)
            if not t:
                continue
            txt_path = storage._video_path(creator, video) / "transcript.txt"  # noqa: SLF001
            preview = (t.text or "").strip().splitlines()[0][:80] if t.text else "(empty)"
            print(f"\n  {video.id}: {video.title!r}")
            print(f"    transcript.json : {len(t.segments)} segments, {len(t.text)} chars")
            print(f"    transcript.txt  : {txt_path}")
            print(f"    preview         : {preview}")
    finally:
        repo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transcribe a creator's latest downloaded videos.")
    parser.add_argument("creator_id", help="Creator ID (e.g. douyin_MS4w...)")
    parser.add_argument("--limit", "-n", type=int, default=None, help="Max videos to transcribe")
    args = parser.parse_args()
    main(args.creator_id, args.limit)
