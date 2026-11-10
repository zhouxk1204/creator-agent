#!/usr/bin/env bash
# Translate synopsis and tags, then correct and retranslate subtitles with one LLM server.
#
#   ./06_correct_subtitles.sh ~/Desktop/doraemon_941
#   ./06_correct_subtitles.sh ~/Desktop/doraemon_941/941_1_origin.srt
#   ./06_correct_subtitles.sh ~/Desktop/doraemon_941 --force
#
# With no argument, scripts/correct_doraemon_subtitles.py uses the episode in
# logs/.last_fetched and processes every *_origin.srt in its Desktop folder.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

uv run python scripts/correct_doraemon_subtitles.py "$@"
