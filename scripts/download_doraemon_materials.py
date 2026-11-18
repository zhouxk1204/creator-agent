"""Run the latest Doraemon video pipeline alongside the materials fetch."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import fetch_doraemon
import httpx

ROOT = Path(__file__).resolve().parents[1]
UPSCALE_PYTHON = Path(r"C:\Users\34696\miniconda3\envs\upscale\python.exe")


def _save_materials_dir(materials: Path) -> None:
    state = fetch_doraemon.STATE_DIR / ".last_doraemon_materials"
    state.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=state.parent, delete=False) as handle:
            temp = Path(handle.name)
            handle.write(str(materials) + "\n")
        os.replace(temp, state)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _run_script(name: str, *args: str, python: Path | str = sys.executable) -> int:
    return subprocess.run([str(python), str(ROOT / "scripts" / name), *args], cwd=ROOT, check=False).returncode


def _video_pipeline(episode: str, airdate: str, materials: Path) -> int:
    rc = _run_script("download_tver.py", "--out-dir", str(materials), "--name", episode, "--broadcast-date", airdate)
    if rc:
        return rc
    rc = _run_script("split_episode.py", str(materials / f"{episode}.mp4"), "-o", str(materials), "--confirm-anomaly")
    if rc == 2:
        print(f"Split cancelled - preview folder: {materials / 'preview'}", flush=True)
        return 0
    if rc:
        return rc

    clips = [materials / f"{episode}_{index}.mp4" for index in (1, 2)]
    missing = [clip for clip in clips if not clip.is_file()]
    if missing:
        for clip in missing:
            print(f"[upscale] Missing split clip: {clip}", flush=True)
        return 1
    if not UPSCALE_PYTHON.is_file():
        print(f"[upscale] Python not found: {UPSCALE_PYTHON}", flush=True)
        return 1
    return _run_script(
        "upscale_tool.py", *(str(clip) for clip in clips), "--output-dir", str(materials), python=UPSCALE_PYTHON
    )


def latest_materials(*, download_video: bool = True) -> int:
    item = fetch_doraemon._latest_item()
    if item is None:
        print("No already-broadcast episode found in the story index.", flush=True)
        return 1
    episode = str(int(item["id"]))
    airdate = item["date"]
    materials = fetch_doraemon.desktop_episode_dir(episode).resolve()
    materials.mkdir(parents=True, exist_ok=True)
    _save_materials_dir(materials)
    print(f"Episode {episode} (aired {airdate})\n[materials] {materials}", flush=True)

    if not download_video:
        print("Video download, split and video upscale skipped.", flush=True)
        return _run_script("fetch_doraemon.py", episode)

    materials_log = materials / "materials_log.txt"
    try:
        log = materials_log.open("w", encoding="utf-8")
    except OSError as exc:
        print(f"[WARNING] Cannot open materials log; fetch skipped: {exc}", flush=True)
        return _video_pipeline(episode, airdate, materials)

    fetch_proc = None
    with log:
        try:
            fetch_proc = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "scripts/fetch_doraemon.py"), episode],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            print(f"[materials] Fetching titles, synopses and covers in parallel - log: {materials_log}", flush=True)
        except OSError as exc:
            print(f"[WARNING] Cannot start materials fetch: {exc}", flush=True)

        try:
            rc = _video_pipeline(episode, airdate, materials)
        finally:
            # Keep the log open until the worker exits, including when the video
            # branch fails or the interactive split is cancelled.
            if fetch_proc is not None:
                if fetch_proc.poll() is None:
                    print("[materials] Waiting for the parallel fetch to finish...", flush=True)
                fetch_rc = fetch_proc.wait()
                if fetch_rc:
                    print(
                        f"[WARNING] Materials fetch failed (exit {fetch_rc}); video result unchanged. "
                        f"See {materials_log}",
                        flush=True,
                    )
                else:
                    print(f"[materials] Titles, synopses and covers ready: {materials}", flush=True)

    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-video", action="store_true", help="fetch only the latest episode's materials")
    args = parser.parse_args(argv)
    try:
        return latest_materials(download_video=not args.no_video)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"[ERROR] {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
