#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

EP="${1:-}"

if [ -n "$EP" ]; then
    uv run python scripts/fetch_doraemon.py "$EP"
    exit 0
fi

uv run python scripts/fetch_doraemon.py --latest
EP="$(tr -d '[:space:]' < logs/.last_fetched)"
AIRDATE="$(tr -d '[:space:]' < logs/.last_fetched_date 2>/dev/null || true)"

echo
echo "========================================"
echo "  Episode $EP (aired $AIRDATE) - full pipeline"
echo "========================================"
echo "Synopsis translation runs later in 06_correct_subtitles.sh."

MAT="$HOME/Desktop/doraemon_$EP"
mkdir -p "$MAT"
echo "[materials] $MAT"
MATERIALS_STATE="logs/.last_doraemon_materials"
MATERIALS_TMP="${MATERIALS_STATE}.$$"
trap 'rm -f "$MATERIALS_TMP"' EXIT
printf '%s\n' "$MAT" > "$MATERIALS_TMP"
mv -f "$MATERIALS_TMP" "$MATERIALS_STATE"
trap - EXIT

DATEARG=()
if [ -n "$AIRDATE" ]; then
    DATEARG=(--broadcast-date "$AIRDATE")
fi
uv run python scripts/download_tver.py --out-dir "$MAT" --name "$EP" "${DATEARG[@]}"

RC=0
uv run python scripts/split_episode.py "$MAT/$EP.mp4" -o "$MAT" --confirm-anomaly || RC=$?
if [ "$RC" = "2" ]; then
    echo "Split cancelled - preview folder: $MAT/preview"
    RC=0
fi

echo
echo "Done! Materials: $MAT"
open "$MAT" 2>/dev/null || true
exit "$RC"
