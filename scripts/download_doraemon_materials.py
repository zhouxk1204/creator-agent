"""Run or resume the Doraemon video pipeline alongside a live materials fetch."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import fetch_doraemon
import httpx
from live_logging import LiveLog, LoggedProcess, install_interrupt_handler, run_logged
from upscale_tool import is_complete

ROOT = Path(__file__).resolve().parents[1]
UPSCALE_PYTHON = Path(r"C:\Users\34696\miniconda3\envs\upscale\python.exe")
STATE_NAME = "pipeline_state.json"
PENDING_MARKER = ".last_doraemon_pipeline"
PENDING_STATUSES = {"running", "failed", "interrupted", "cancelled"}


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _save_materials_dir(materials: Path) -> None:
    _atomic_text(fetch_doraemon.STATE_DIR / ".last_doraemon_materials", str(materials) + "\n")


def _save_state(materials: Path, episode: str, airdate: str, stage: str, status: str) -> None:
    state = {
        "schema_version": 1,
        "episode": episode,
        "broadcast_date": airdate,
        "stage": stage,
        "status": status,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    _atomic_text(materials / STATE_NAME, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _pending_run() -> tuple[str, str, Path] | None:
    # Materials-only runs update the materials marker independently.
    # Keep the last full pipeline checkpoint separate so those fetches cannot hide it.
    marker = fetch_doraemon.STATE_DIR / PENDING_MARKER
    if not marker.is_file():
        marker = fetch_doraemon.STATE_DIR / ".last_doraemon_materials"
    if not marker.is_file():
        return None
    try:
        value = marker.read_text(encoding="utf-8-sig").strip()
        if not value:
            return None
        materials = Path(value).expanduser().resolve()
        state_path = materials / STATE_NAME
        if not state_path.is_file():
            return None
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("unsupported pipeline state")
        episode = str(state.get("episode", ""))
        if not episode.isdigit() or int(episode) <= 0:
            raise ValueError("invalid saved episode")
        airdate = date.fromisoformat(state["broadcast_date"]).isoformat()
        if state.get("status") in PENDING_STATUSES:
            return str(int(episode)), airdate, materials
    except (OSError, ValueError, KeyError, TypeError) as exc:
        LiveLog(None, "pipeline").event(f"[WARNING] Cannot resume saved task: {exc}; resolving latest episode instead")
    return None


def _run_script(
    name: str,
    *args: str,
    python: Path | str = sys.executable,
    log_file: Path | None = None,
) -> int:
    command = [str(python), "-u", str(ROOT / "scripts" / name), *args]
    return run_logged(
        command, LiveLog(log_file, name.removesuffix(".py")), cwd=ROOT, interactive=name == "split_episode.py"
    )


def _video_pipeline(episode: str, airdate: str, materials: Path) -> int:
    logger = LiveLog(materials / "pipeline_log.txt", "pipeline")
    clips = [materials / f"{episode}_{index}.mp4" for index in (1, 2)]
    steps = [
        (
            "download",
            "1/3 TVer download (reuse completed video)",
            "download_tver.py",
            ["--out-dir", str(materials), "--name", episode, "--broadcast-date", airdate],
            sys.executable,
        ),
        (
            "split",
            "2/3 story splitting (reuse completed split)",
            "split_episode.py",
            [str(materials / f"{episode}.mp4"), "-o", str(materials), "--confirm-anomaly"],
            sys.executable,
        ),
        (
            "upscale",
            "3/3 Real-ESRGAN (skip completed videos, restart unfinished video)",
            "upscale_tool.py",
            [*(str(clip) for clip in clips), "--output-dir", str(materials)],
            UPSCALE_PYTHON,
        ),
    ]
    for stage, description, script, args, python in steps:
        _save_state(materials, episode, airdate, stage, "running")
        logger.event(f"[STAGE START] {description}")
        started = time.monotonic()
        if stage == "upscale":
            if all(is_complete(clip, materials) for clip in clips):
                logger.event("[SKIP] Both upscaled videos are complete; no GPU process is needed")
                continue
            missing = [clip for clip in clips if not clip.is_file() or clip.stat().st_size == 0]
            if missing or not UPSCALE_PYTHON.is_file():
                for clip in missing:
                    logger.event(f"[ERROR] Missing split clip: {clip}")
                if not UPSCALE_PYTHON.is_file():
                    logger.event(f"[ERROR] Upscale Python not found: {UPSCALE_PYTHON}")
                _save_state(materials, episode, airdate, stage, "failed")
                return 1
        try:
            rc = _run_script(script, *args, python=python, log_file=logger.path)
        except BaseException as exc:
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            _save_state(materials, episode, airdate, stage, status)
            logger.event(f"[{status.upper()}] {stage}: {exc}; reopen 01_download_latest_materials.bat to continue")
            raise
        logger.event(f"[STAGE END] {stage}; exit={rc}; elapsed={time.monotonic() - started:.1f}s")
        if stage == "split" and rc == 2:
            _save_state(materials, episode, airdate, stage, "cancelled")
            logger.event(f"Split cancelled - preview folder: {materials / 'preview'}")
            return 0
        if rc:
            _save_state(materials, episode, airdate, stage, "interrupted" if rc == 130 else "failed")
            logger.event("[RESUME] Completed files are retained. Reopen the same bat to retry this episode.")
            return rc
    _save_state(materials, episode, airdate, "materials", "running")
    logger.event("[COMPLETE] Video pipeline finished; materials branch still needs to finish")
    return 0


def latest_materials(*, download_video: bool = True, prefer_resume: bool = True) -> int:
    pending = _pending_run() if download_video and prefer_resume else None
    if pending is not None:
        episode, airdate, materials = pending
    else:
        item = fetch_doraemon._latest_item()
        if item is None:
            LiveLog(None, "pipeline").event("No already-broadcast episode found in the story index.")
            return 1
        episode, airdate = str(int(item["id"])), item["date"]
        materials = fetch_doraemon.desktop_episode_dir(episode).resolve()
    materials.mkdir(parents=True, exist_ok=True)
    _save_materials_dir(materials)
    logger = LiveLog(materials / "pipeline_log.txt", "pipeline")
    logger.event(f"===== {'RESUME' if pending else 'NEW RUN'} episode={episode}; aired={airdate} =====")
    logger.event(f"[MATERIALS] {materials}")
    logger.event("[LOGS] pipeline_log.txt, materials_log.txt, run_log.txt (append, timestamped)")
    if pending:
        logger.event("[RESUME] Using saved episode without querying the latest index; completed outputs will be reused")

    if not download_video:
        logger.event("Video download, split and video upscale skipped.")
        return _run_script("fetch_doraemon.py", episode, log_file=materials / "materials_log.txt")

    _save_state(materials, episode, airdate, "starting", "running")
    _atomic_text(fetch_doraemon.STATE_DIR / PENDING_MARKER, str(materials) + "\n")
    materials_logger = LiveLog(materials / "materials_log.txt", "materials")
    fetch_proc = None
    fetch_rc = None
    reached_materials = False
    try:
        try:
            fetch_proc = LoggedProcess(
                [sys.executable, "-u", str(ROOT / "scripts/fetch_doraemon.py"), episode], materials_logger, cwd=ROOT
            )
            logger.event("[PARALLEL] Titles, synopses and covers now stream live in the console and materials_log.txt")
        except OSError as exc:
            logger.event(f"[WARNING] Cannot start materials fetch: {exc}; video pipeline continues")

        rc = _video_pipeline(episode, airdate, materials)
        reached_materials = True
        if fetch_proc is not None:
            if rc == 130:
                fetch_proc.stop()
            else:
                if fetch_proc.poll() is None:
                    logger.event("[MATERIALS] Waiting for parallel fetch; its progress remains visible")
                fetch_rc = fetch_proc.wait()
                if fetch_rc:
                    logger.event(
                        f"[WARNING] Materials fetch failed (exit {fetch_rc}); video result unchanged. "
                        "See materials_log.txt"
                    )
                else:
                    logger.event("[MATERIALS] Titles, synopses and covers ready")
    except BaseException as exc:
        if fetch_proc is not None:
            fetch_proc.stop()
        if reached_materials:
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            _save_state(materials, episode, airdate, "materials", status)
        if isinstance(exc, KeyboardInterrupt):
            logger.event("[INTERRUPTED] Completed videos are retained; reopen this bat to resume this episode")
        raise

    state = json.loads((materials / STATE_NAME).read_text(encoding="utf-8"))
    if rc == 0 and state["status"] != "cancelled":
        if fetch_rc == 0:
            _save_state(materials, episode, airdate, "done", "completed")
        else:
            _save_state(materials, episode, airdate, "materials", "failed")
            logger.event(
                "[RESUME] Materials are unfinished; reopen the same bat to retry while reusing completed videos"
            )
    logger.event(f"===== RUN END exit={rc} =====")
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-video", action="store_true", help="fetch only the latest episode's materials")
    parser.add_argument(
        "--latest", action="store_true", help="select latest episode instead of resuming the saved task"
    )
    args = parser.parse_args(argv)
    try:
        return latest_materials(download_video=not args.no_video, prefer_resume=not args.latest)
    except KeyboardInterrupt:
        LiveLog(None, "pipeline").event("[INTERRUPTED] Exit 130. Run the same bat again to continue the saved task.")
        return 130
    except (OSError, ValueError, httpx.HTTPError) as exc:
        LiveLog(None, "pipeline").event(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    install_interrupt_handler()
    sys.exit(main())
