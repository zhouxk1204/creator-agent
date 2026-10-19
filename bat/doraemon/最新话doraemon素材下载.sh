#!/usr/bin/env bash
# macOS/Linux twin of 最新话doraemon素材下载.bat: latest-episode Doraemon
# materials pipeline.
#
#   ./最新话doraemon素材下载.sh         no number: FULL pipeline for the latest
#                                      aired episode - fetch story info ->
#                                      translate -> TVer download (<ep>.mp4)
#                                      -> split (contact-sheet y/n confirm)
#   ./最新话doraemon素材下载.sh 934    number given: story info + translation
#                                      only - no download, no split
#
# All materials land in ~/Desktop/doraemon_<ep>/. Differences from the .bat:
# the translation LLM server is never started/stopped here - translation runs
# only when a server is already healthy on 127.0.0.1:8080.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

translate_ep() {
    if curl -sf -m 3 http://127.0.0.1:8080/health >/dev/null 2>&1; then
        uv run python scripts/translate_doraemon.py "$1"
    else
        echo "[server] translation LLM not running on 127.0.0.1:8080 - skipping translation."
    fi
}

EP="${1:-}"

if [ -n "$EP" ]; then
    # Explicit episode: story info + translation only (no download / split).
    uv run python scripts/fetch_doraemon.py "$EP"
    translate_ep "$EP"
    exit 0
fi

# ---------------------------------------------------------------------------
# Full pipeline: latest story -> translate -> TVer download -> split
# ---------------------------------------------------------------------------
uv run python scripts/fetch_doraemon.py --latest
EP="$(tr -d '[:space:]' < storage/doraemon/.last_fetched)"
AIRDATE="$(tr -d '[:space:]' < storage/doraemon/.last_fetched_date 2>/dev/null || true)"

echo
echo "========================================"
echo "  Episode $EP (aired $AIRDATE) - full pipeline"
echo "========================================"

translate_ep "$EP" || echo "[translate] failed - continuing pipeline."

MAT="$HOME/Desktop/doraemon_$EP"
# Already populated by fetch_doraemon.py (story.md, metadata.json, images,
# covers) and translate_doraemon.py (translated {ep}_{i}_zh.md notes with
# tags); the download + split steps below add the video to the SAME folder.
mkdir -p "$MAT"
echo "[materials] $MAT"

DATEARG=()
if [ -n "$AIRDATE" ]; then
    DATEARG=(--broadcast-date "$AIRDATE")
fi
uv run python scripts/download_tver.py --out-dir "$MAT" --name "$EP" "${DATEARG[@]}"

RC=0
uv run python scripts/split_episode.py "$MAT/$EP.mp4" -o "$MAT" --confirm || RC=$?
if [ "$RC" = "2" ]; then
    echo "Split cancelled - preview folder: $MAT/preview"
    RC=0
fi

echo
echo "Done! Materials: $MAT"
open "$MAT" 2>/dev/null || true
exit "$RC"
