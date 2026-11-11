#!/usr/bin/env bash
# Translate synopsis and tags, then correct and retranslate subtitles with one LLM server.
#
#   ./06_correct_subtitles.sh ~/Desktop/doraemon_941
#   ./06_correct_subtitles.sh ~/Desktop/doraemon_941/941_1_origin.srt
#   ./06_correct_subtitles.sh ~/Desktop/doraemon_941 --force
#
# With no argument, scripts/correct_doraemon_subtitles.py uses the exact folder in
# logs/.last_doraemon_materials, falling back to the Desktop episode folder from
# logs/.last_fetched only when no materials folder has been recorded.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

uv run python scripts/correct_doraemon_subtitles.py "$@"
