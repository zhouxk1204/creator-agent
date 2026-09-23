#!/usr/bin/env bash
# Fetch a Doraemon episode page (title / synopsis / images) from TV Asahi.
# Usage: sh/doraemon.sh 934   (no arg = prompt for episode number)
set -euo pipefail
cd "$(dirname "$0")/.."

ep="${1:-}"
if [ -z "$ep" ]; then
    read -rp "Episode number (e.g. 934): " ep
fi
if [ -z "$ep" ]; then
    echo "No episode number given." >&2
    exit 1
fi

uv run python scripts/fetch_doraemon.py "$ep"
