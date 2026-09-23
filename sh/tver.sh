#!/usr/bin/env bash
# Download TVer episodes (default: latest Doraemon).
# Usage: sh/tver.sh [--list|--all|--episode <id>|--series <id_or_url>]
set -euo pipefail
cd "$(dirname "$0")/.."

uv run python scripts/download_tver.py "$@"
