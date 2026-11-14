"""Download TVer episodes (default: latest Doraemon).

Discovery flow: tv-asahi.co.jp/doraemon has a TVer vod banner pointing at the
series page (https://tver.jp/series/srtsxzl3si). That banner is JS-rendered, so
instead of scraping it we call TVer's internal API directly with the series id:

    service-api.tver.jp/api/v1/callSeriesSeasons/{series_id}
    service-api.tver.jp/api/v1/callSeasonEpisodes/{season_id}

The video itself is downloaded with yt-dlp (TVer serves unencrypted HLS).

Output: <out_dir>/<name>.mp4 only (no thumbnail or info-json sidecars).

Usage:
    uv run python scripts/download_tver.py                 # latest Doraemon episode
    uv run python scripts/download_tver.py --list          # list available episodes only
    uv run python scripts/download_tver.py --all           # all available episodes
    uv run python scripts/download_tver.py --series <id_or_series_url>
    uv run python scripts/download_tver.py --episode epenb4xglc
    uv run python scripts/download_tver.py --out-dir DIR --name 938 \
        --broadcast-date 2026-10-17                        # pipeline mode: pick the
                                                           # episode aired that day,
                                                           # save as DIR/938.mp4

--broadcast-date guards against a TVer/story-page mismatch: TVer's latest can
lag the aired episode by a day or two, so the materials pipeline passes the
broadcast date from the story page and only an episode whose
broadcastDateLabel matches (e.g. "10月17日(土)放送分") is downloaded.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

API_BASE = "https://service-api.tver.jp/api/v1"
API_HEADERS = {"x-tver-platform-type": "web", "User-Agent": "Mozilla/5.0"}
DEFAULT_SERIES = "srtsxzl3si"  # ドラえもん — the TVer link on tv-asahi.co.jp/doraemon
# Fixed download location: desktop raw-video folder (per-series subdir below it).
OUT_ROOT = Path("C:/Users/34696/Desktop/哆啦a梦生肉")

# Make non-ASCII print correctly on the Windows console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _series_id(arg: str) -> str:
    m = re.search(r"/series/([a-z0-9]+)", arg)
    return m.group(1) if m else arg.strip()


def list_episodes(client: httpx.Client, series_id: str) -> list[dict]:
    """Available episodes of a series.

    service-api.tver.jp drops TLS connections now and then (SSL EOF) even when
    the video CDN is fine, so the listing gets the same retry treatment as the
    download: without it a rerun fails before it can even decide to skip.
    """
    for attempt in range(1, 4):
        try:
            resp = client.get(f"{API_BASE}/callSeriesSeasons/{series_id}", headers=API_HEADERS)
            resp.raise_for_status()
            seasons = resp.json()["result"]["contents"]

            episodes = []
            for season in seasons:
                if season["type"] != "season":
                    continue
                season_id = season["content"]["id"]
                resp = client.get(f"{API_BASE}/callSeasonEpisodes/{season_id}", headers=API_HEADERS)
                resp.raise_for_status()
                for item in resp.json()["result"]["contents"]:
                    if item["type"] == "episode":
                        episodes.append(item["content"])
            return episodes
        except httpx.HTTPError as exc:
            if attempt == 3:
                raise
            print(f"  [retry {attempt}/2] listing failed ({exc.__class__.__name__}: {exc}) — retrying")
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def _ffmpeg_location() -> str | None:
    """Reuse the ffmpeg configured for ASR (settings.yaml), if it exists."""
    try:
        from creator_agent.config import load_settings

        path = Path(load_settings().asr.ffmpeg_path)
        if path.exists():
            return str(path.parent)
    except Exception:
        pass
    return None


def _open_dir(path: Path) -> None:
    """Open the download folder for the user (Windows shell only)."""
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]


# A finished TVer episode is ~600 MB at 1080p; anything under this is a leftover
# fragment from an interrupted run and must be downloaded again.
MIN_FINISHED_BYTES = 50 * 1024 * 1024


def _already_downloaded(out_dir: Path, name: str) -> Path | None:
    """The finished ``<name>.mp4`` when it is complete, else ``None``.

    Reruns must be cheap: pipeline launchers are re-run by hand whenever a later
    stage fails, and re-downloading 600 MB (or clobbering a good file because the
    network hiccuped again) is exactly what this avoids. ``--force`` overrides.
    """
    target = out_dir / f"{name}.mp4"
    if target.is_file() and target.stat().st_size >= MIN_FINISHED_BYTES:
        return target
    return None


def _report_skip(done: Path) -> None:
    size = done.stat().st_size / 1024 / 1024
    print(f"  [skip] already downloaded: {done.name} ({size:.1f} MiB) — --force to redo")


def download(episode_id: str, out_dir: Path, name: str | None = None, force: bool = False) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if name and not force:
        done = _already_downloaded(out_dir, name)
        if done is not None:
            _report_skip(done)
            return
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-write-thumbnail",
        "--no-write-info-json",
        # TVer serves H.264/AAC HLS, so a remux is enough — no re-encode.
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        # Parallel HLS fragments — needs pycryptodomex (decrypts AES-128 in
        # process; without it yt-dlp delegates to ffmpeg, which is sequential).
        "-N",
        "8",
        # TVer connection is flaky; retry metadata + fragments harder.
        "--retries",
        "10",
        "--fragment-retries",
        "10",
        "-o",
        f"{name}.%(ext)s" if name else "%(title)s [%(id)s].%(ext)s",
        "--paths",
        str(out_dir),
    ]
    ffmpeg = _ffmpeg_location()
    if ffmpeg:
        cmd += ["--ffmpeg-location", ffmpeg]
    cmd.append(f"https://tver.jp/episodes/{episode_id}")
    print(f"  $ {' '.join(cmd[2:])}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"yt-dlp failed for episode {episode_id} (exit code {e.returncode}). "
            "Usually a transient network reset to tver.jp — just retry. If it keeps "
            "failing, route through a Japanese proxy, e.g. set HTTPS_PROXY=http://host:port "
            "before running (yt-dlp honors it)."
        ) from e


def main() -> None:
    parser = argparse.ArgumentParser(description="Download TVer episodes (default: latest Doraemon).")
    parser.add_argument(
        "--series", default=DEFAULT_SERIES, help="Series id or full URL (default: Doraemon srtsxzl3si)."
    )
    parser.add_argument("--episode", help="Download one episode id directly, skip listing.")
    parser.add_argument("--all", action="store_true", help="Download all available episodes.")
    parser.add_argument("--list", action="store_true", help="List available episodes and exit.")
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="Download here instead of the fixed raw-video folder."
    )
    parser.add_argument(
        "--name", default=None, help="Output filename without extension (default: '<title> [<episode id>]')."
    )
    parser.add_argument(
        "--broadcast-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only download the available episode aired on this date "
        "(matches broadcastDateLabel, e.g. 2026-10-17 -> '10月17日').",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Download even when <out-dir>/<name>.mp4 already exists (default: skip finished files).",
    )
    args = parser.parse_args()

    series_id = _series_id(args.series)
    # The default Doraemon series lands directly in the fixed folder; any other
    # series gets its own subdir under it to keep files from mixing.
    out_dir = args.out_dir or (OUT_ROOT if series_id == DEFAULT_SERIES else OUT_ROOT / series_id)

    if args.episode:
        download(args.episode, out_dir, name=args.name, force=args.force)
        _open_dir(out_dir)
        return

    # With a known output name the file on disk settles it: no API call, no
    # retries, no risk of a TLS failure turning a finished episode into an error.
    if args.name and not args.force and not args.list:
        done = _already_downloaded(out_dir, args.name)
        if done is not None:
            _report_skip(done)
            print(f"\nSaved to: {out_dir.resolve()}")
            return

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        episodes = list_episodes(client, series_id)
    available = [ep for ep in episodes if ep.get("isAvailable")]

    if not available:
        print(f"No available episodes for series {series_id}.")
        return

    print(f"Series {series_id}: {len(available)} available episode(s)")
    for ep in available:
        end = datetime.fromtimestamp(ep["endAt"]).strftime("%Y-%m-%d %H:%M") if ep.get("endAt") else "?"
        mins = (ep.get("duration") or 0) // 60
        print(f"  {ep['id']}  {ep.get('broadcastDateLabel', '')}  {mins}min  until {end}  {ep.get('title', '')}")

    if args.list:
        return

    targets = available if args.all else available[:1]
    if args.broadcast_date:
        try:
            _, b_month, b_day = (int(p) for p in args.broadcast_date.split("-"))
        except ValueError:
            sys.exit(f"Invalid --broadcast-date {args.broadcast_date!r} (expected YYYY-MM-DD)")
        needle = f"{b_month}月{b_day}日"
        targets = [ep for ep in available if needle in ep.get("broadcastDateLabel", "")]
        if not targets:
            print(f"\nNo available episode matches broadcast date {args.broadcast_date} ({needle}).")
            print("TVer may not have posted it yet — retry later.")
            sys.exit(1)
        if not args.all:
            targets = targets[:1]
    for ep in targets:
        print(f"\nDownloading {ep['id']}: {ep.get('title', '')}")
        download(ep["id"], out_dir, name=args.name, force=args.force)

    print(f"\nSaved to: {out_dir.resolve()}")
    _open_dir(out_dir)


if __name__ == "__main__":
    main()
