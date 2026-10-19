#!/usr/bin/env bash
# macOS/Linux twin of 02_fetch_doraemon.bat: fetch a Doraemon episode's story
# info from TV Asahi, then translate the per-story notes to Chinese.
#
#   ./02_fetch_doraemon.sh         # latest already-aired episode (default)
#   ./02_fetch_doraemon.sh 934     # explicit episode number
#
# With no argument the episode is resolved from the backnumber index
# (doraemon/json/story.json): the newest entry whose broadcast date is not in
# the future. Fetched content lands in storage/doraemon/{ep}/ and is mirrored
# to ~/Desktop/doraemon_story/{ep}/. The translation step runs only when the
# local LLM server (llama.cpp on 127.0.0.1:8080) is already up — unlike the
# .bat, this script never starts/stops the server itself.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

EP="${1:-}"
if [ -z "$EP" ]; then
    EP="--latest"
fi

uv run python scripts/fetch_doraemon.py "$EP"

# When --latest resolved the number for us, read it back for translation.
if [ "$EP" = "--latest" ]; then
    EP="$(tr -d '[:space:]' < storage/doraemon/.last_fetched)"
fi

if curl -sf -m 3 http://127.0.0.1:8080/health >/dev/null 2>&1; then
    uv run python scripts/translate_doraemon.py "$EP"
else
    echo "[server] translation LLM not running on 127.0.0.1:8080 - skipping translation."
fi
