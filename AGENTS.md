# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Current State

**No code is implemented yet.** The repo contains only the Phase 1 design spec at `docs/superpowers/specs/2026-07-09-creator-intelligence-agent-phase1-design.md`. That spec is the authoritative source of truth for architecture, models, interfaces, and scope — read it before making any non-trivial change.

The git repo has no commits yet on `main`.

## What This Project Is

Creator Intelligence Agent — a daily sync pipeline that collects videos from specified creators (Douyin first, Bilibili/Youtube later), downloads video/cover/metadata, and (in Phase 2+) runs ASR / vision / style / DNA analysis.

Phase 1 is **collection-only**: no AI analysis, just `collect → download` with state-machine-driven resumability.

## Tech Stack & Commands (planned)

Python 3.12, managed with **uv**. Ruff for lint+format. pytest for tests.

```bash
uv sync                                          # install deps
uv run creator-agent auth login                  # interactive Douyin login (headless=False)
uv run creator-agent doctor                      # env / browser / disk checks
uv run creator-agent creator add "<url>"         # register a creator
uv run creator-agent creator list
uv run creator-agent sync                        # sync yesterday's videos for all creators
uv run creator-agent sync --creator douyin_12345 --days 3

uv run playwright install chromium               # first-time browser setup
uv run ruff check .                              # lint
uv run ruff format .                             # format
uv run pytest                                    # all tests
uv run pytest tests/unit/test_time_parser.py     # single file
uv run pytest -k "test_scroll_strategy"          # single test by name
uv run pytest -m "not e2e"                       # skip e2e (default; e2e hits real Douyin)
```

Daily sync runs via system crontab (not in-process scheduler): `0 2 * * * cd /path/to/creator-agent && uv run creator-agent sync`.

## Architecture (read the spec for full detail)

Six principles govern the design — follow them when extending:

1. **Pluggable collectors** — `collector/base.py` is the ABC; `collector/douyin/` is the first impl. New platforms = new subpackage, no upstream changes.
2. **Platform independence** — collectors return `CollectedVideo`; downstream code never branches on platform.
3. **Filter abstraction** — collectors don't know "yesterday". They take a `CollectFilter(start, end, max_videos)`. Time-window logic lives in filter factories (`yesterday_filter`, `last_n_days_filter`).
4. **State machine driven** — every `Video` row has a `status` (`NEW → METADATA_SAVED → VIDEO_DOWNLOADED`). Failures stop at the last success; next `sync` resumes from there. **This is the core value proposition — don't break it.**
5. **File + index separation** — media and JSON go to `storage/{creator_id}/videos/{video_id}/`; SQLite stores only index + status + sync_history.
6. **Partial failure is OK** — one video/creator failing must not block others. `PipelineRunner.sync_creator` catches per-video exceptions and records them in `sync_history.error`.

## Key layout (from spec)

```
src/creator_agent/
├── models/         # Creator, CollectedVideo, Video, VideoStats, VideoAsset, VideoStatus
├── storage/        # FileStorage: profile.json, video.mp4, cover.jpg, metadata.json
├── repository/     # SQLite Repository: creator, video, sync_history
├── browser/        # BrowserManager (Playwright sync API, persistent context)
├── collector/      # base.py (ABC) + douyin/{collector,parser,time_parser,selectors/}
├── downloader/     # httpx+cookies first, Playwright intercept as fallback
├── pipeline/       # PipelineRunner — the state machine driver
├── scheduler/      # run_once only; crontab for daily
└── cli/            # Typer app
```

## Phase boundaries

**Phase 1 (now)**: Tasks 1–7 + minimal scheduler + minimal CLI. Terminal status is `VIDEO_DOWNLOADED`.

**Phase 2 (later)**: ASR (FunASR), Vision (Qwen2.5-VL), StyleAnalyzer, ContentDNA Engine, Report, in-process APScheduler. Extending requires exactly 3 steps: add `VideoStatus` enum value, add module handler, hook into `PipelineRunner`. Don't add Phase 2 modules during Phase 1 work.

## Things to watch out for

- **Selectors are split across files** (`collector/douyin/selectors/{profile,video,common}.py`) so DOM changes don't require touching parser logic. Parser unit tests use HTML fixtures, not live pages.
- **`time_parser.py`** handles Douyin's relative time strings ("2小时前" / "昨天" / "3天前" / "2024-01-01") → UTC datetime. Test it thoroughly — bad time parsing breaks the Filter window logic.
- **`OUT_OF_WINDOW_PAGE_THRESHOLD = 2`** — scroll stops after 2 consecutive pages with no in-window videos. Don't lower this; it guards against transient empty pages.
- **Scroll delays are randomized 1–3s** to avoid rate limiting. Don't make them deterministic.
- **Browser profile is dedicated** at `storage/.browser_profile/`, not the system Chrome. `auth login` opens headful once; subsequent runs are headless.
- **Video URLs expire** — collect then download immediately in the same sync run; don't queue URLs for later.
- **Exception hierarchy**: `CreatorAgentError` → `{Collector,Downloader,Storage,Repository}Error`. Selector-not-found is NOT retried; nav/download timeouts retry 3× with exponential backoff.

## Stable contracts (don't break these casually)

`CollectFilter`, `CollectedVideo`, and `Collector.collect(...)` are the seams Phase 2 modules build on. Changing their signatures blocks parallel development — treat as API.
