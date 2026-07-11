from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from loguru import logger as loguru_logger

from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.collector.base import last_n_days_filter, yesterday_filter
from creator_agent.config import load_settings
from creator_agent.downloader.downloader import Downloader
from creator_agent.models.creator import Creator
from creator_agent.pipeline.runner import PipelineRunner
from creator_agent.repository.sqlite_repo import Repository
from creator_agent.storage.file_storage import FileStorage

app = typer.Typer(
    name="creator-agent",
    help="Creator Intelligence Agent - daily sync pipeline for creator video collection.",
    no_args_is_help=True,
)
creator_app = typer.Typer(help="Manage creators.")
app.add_typer(creator_app, name="creator")


def _init_components(storage_dir=None, db_path=None):
    settings = load_settings()
    if storage_dir:
        settings.storage_dir = storage_dir
    if db_path:
        settings.db_path = db_path

    loguru_logger.remove()
    loguru_logger.add(
        sys.stderr,
        level=settings.log.level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
    )
    loguru_logger.add(
        settings.log.file,
        level=settings.log.level,
        rotation=settings.log.rotation,
        retention=settings.log.retention,
        encoding="utf-8",
    )
    logging.basicConfig(handlers=[], level=logging.DEBUG)

    storage = FileStorage(settings.storage_dir)
    repo = Repository(settings.db_path)
    browser_config = BrowserConfig(
        user_data_dir=settings.browser.user_data_dir,
        headless=settings.browser.headless,
    )
    browser = BrowserManager(browser_config)
    downloader = Downloader(
        storage=storage,
        browser=browser,
        timeout_sec=settings.downloader.timeout_sec,
        retries=settings.downloader.retry,
    )
    runner = PipelineRunner(repo=repo, storage=storage, browser=browser, downloader=downloader)
    return settings, repo, browser, runner


@app.command()
def doctor():
    typer.echo("Running environment checks...")
    all_pass = True

    py_ok = sys.version_info >= (3, 12)
    status = "OK" if py_ok else "FAIL"
    typer.echo(f"  {status} Python {sys.version_info.major}.{sys.version_info.minor}")
    if not py_ok:
        all_pass = False

    uv_path = shutil.which("uv")
    if uv_path:
        typer.echo(f"  OK uv found at {uv_path}")
    else:
        typer.echo("  FAIL uv not found")
        all_pass = False

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            _ = p.chromium
        typer.echo("  OK Playwright Chromium available")
    except Exception:
        typer.echo("  FAIL Playwright Chromium not installed")
        all_pass = False

    settings = load_settings()
    profile_dir = Path(settings.browser.user_data_dir)
    if profile_dir.exists() and any(profile_dir.iterdir()):
        typer.echo(f"  OK Browser profile exists at {profile_dir}")
    else:
        typer.echo("  FAIL Browser profile not found")
        all_pass = False

    storage_dir = Path(settings.storage_dir)
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        test_file = storage_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        typer.echo(f"  OK Storage directory writable: {storage_dir}")
    except Exception as e:
        typer.echo(f"  FAIL Storage directory not writable: {e}")
        all_pass = False

    db_path = Path(settings.db_path)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        test_db = db_path.parent / ".db_write_test"
        test_db.write_text("test")
        test_db.unlink()
        typer.echo(f"  OK SQLite DB path writable: {db_path}")
    except Exception as e:
        typer.echo(f"  FAIL SQLite DB path not writable: {e}")
        all_pass = False

    try:
        usage = shutil.disk_usage(storage_dir.anchor if storage_dir.anchor else "/")
        free_gb = usage.free / (1024 ** 3)
        disk_ok = free_gb > 1
        status = "OK" if disk_ok else "FAIL"
        typer.echo(f"  {status} Disk space: {free_gb:.1f} GB free")
        if not disk_ok:
            all_pass = False
    except Exception:
        typer.echo("  ? Could not check disk space")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            result = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=5)
            version_line = result.stdout.splitlines()[0] if result.stdout else ""
            typer.echo(f"  OK FFmpeg available: {version_line}")
        except Exception:
            typer.echo("  ? FFmpeg found but failed to run version check")
    else:
        typer.echo("  ! FFmpeg not found (not required for Phase 1)")

    typer.echo()
    if all_pass:
        typer.echo("All checks passed!")
    else:
        typer.echo("Some checks failed - see above.")
    raise typer.Exit(code=0 if all_pass else 1)


@app.command(name="auth-login", help="Open browser for Douyin login (headful).")
def auth_login(
    force: bool = typer.Option(False, "--force", "-f", help="Re-login even if profile exists."),
):
    settings = load_settings()
    profile_dir = Path(settings.browser.user_data_dir)

    if profile_dir.exists() and any(profile_dir.iterdir()) and not force:
        typer.echo(f"Browser profile already exists at {profile_dir}")
        typer.echo("Use --force to re-login.")
        return

    typer.echo("Opening browser for Douyin login...")
    typer.echo("Please log in to Douyin manually in the browser window.")

    config = BrowserConfig(user_data_dir=profile_dir, headless=False)
    with BrowserManager(config) as browser:
        page = browser.new_page()
        page.goto("https://www.douyin.com", wait_until="load", timeout=60000)
        typer.echo("Waiting for login... (press Ctrl+C when done)")
        try:
            page.wait_for_url("https://www.douyin.com/?*", timeout=300000)
            page.wait_for_timeout(5000)
            typer.echo("Login detected!")
        except Exception:
            typer.echo("Login page loaded, cookies should be set.")

    typer.echo(f"Profile saved at {profile_dir}")


@creator_app.command("add")
def creator_add(
    homepage_url: str = typer.Argument(..., help="Creator homepage URL"),
    nickname: Optional[str] = typer.Option(None, "--nickname", "-n", help="Creator nickname"),
):
    settings = load_settings()
    repo = Repository(settings.db_path)

    platform = _detect_platform(homepage_url)
    platform_uid = _extract_uid(homepage_url, platform)
    creator_id = f"{platform}_{platform_uid}"

    existing = repo.get_creator(creator_id)
    if existing:
        typer.echo(f"Creator already exists: {existing.nickname} ({existing.id})")
        return

    creator = Creator(
        id=creator_id,
        platform=platform,
        platform_uid=platform_uid,
        nickname=nickname or platform_uid,
        homepage_url=homepage_url,
        added_at=datetime.now(timezone.utc),
        sync_status="idle",
    )

    if nickname:
        repo.add_creator(creator)
        typer.echo(f"Creator added: {creator.nickname} ({creator.id})")
        return

    typer.echo("Attempting to auto-detect nickname from homepage...")
    browser_config = BrowserConfig(user_data_dir=settings.browser.user_data_dir, headless=True)
    try:
        with BrowserManager(browser_config) as browser:
            page = browser.new_page()
            page.goto(homepage_url, wait_until="load", timeout=30000)
            page.wait_for_timeout(5000)
            detected = page.evaluate("""() => {
                const el = document.querySelector('[class*="nickname"], [class*="username"]');
                return el ? el.textContent.trim() : null;
            }""")
            if detected:
                creator.nickname = detected
                typer.echo(f"  Detected nickname: {detected}")
    except Exception as e:
        typer.echo(f"  Warning: could not auto-detect nickname: {e}")

    repo.add_creator(creator)
    typer.echo(f"Creator added: {creator.nickname} ({creator.id})")


@creator_app.command("remove")
def creator_remove(
    creator_id: str = typer.Argument(..., help="Creator ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    settings = load_settings()
    repo = Repository(settings.db_path)

    creator = repo.get_creator(creator_id)
    if not creator:
        typer.echo(f"Creator not found: {creator_id}")
        raise typer.Exit(code=1)

    if not yes:
        typer.confirm(f"Remove {creator.nickname} ({creator.id}) and ALL data?", abort=True)

    repo.remove_creator(creator_id)

    storage_dir = Path(settings.storage_dir) / creator_id
    if storage_dir.exists():
        shutil.rmtree(storage_dir)
        typer.echo(f"  Removed storage: {storage_dir}")

    typer.echo(f"Creator removed: {creator.nickname} ({creator.id})")


@creator_app.command("list")
def creator_list():
    settings = load_settings()
    repo = Repository(settings.db_path)

    creators = repo.list_creators()
    if not creators:
        typer.echo("No creators registered. Add one with creator-agent creator add.")
        return

    typer.echo(f"{'ID':<30} {'Nickname':<20} {'Platform':<10} {'Status':<10} Last Synced")
    typer.echo("-" * 90)
    for c in creators:
        last_sync = c.last_synced_at.strftime("%Y-%m-%d %H:%M") if c.last_synced_at else "-"
        typer.echo(f"{c.id:<30} {c.nickname:<20} {c.platform:<10} {c.sync_status:<10} {last_sync}")
    typer.echo(f"Total: {len(creators)}")


@app.command()
def sync(
    creator_id: Optional[str] = typer.Option(None, "--creator", "-c", help="Sync specific creator by ID"),
    days: int = typer.Option(1, "--days", "-d", help="Number of days to look back"),
):
    settings, repo, browser, runner = _init_components()

    filter_obj = last_n_days_filter(days) if days > 1 else yesterday_filter()

    try:
        browser.start()

        if creator_id:
            creator = repo.get_creator(creator_id)
            if not creator:
                typer.echo(f"Creator not found: {creator_id}")
                raise typer.Exit(code=1)
            typer.echo(f"Syncing {creator.nickname} ({creator.id})...")
            result = runner.sync_creator(creator, filter_obj)
            typer.echo(
                f"  Collected: {result.collected}, "
                f"Downloaded: {result.downloaded}, "
                f"Failed: {len(result.failed)}"
            )
        else:
            from creator_agent.scheduler.scheduler import Scheduler
            scheduler = Scheduler(runner=runner, repo=repo)
            count = scheduler.run_once(filter_obj)
            typer.echo(f"Synced {count} creators.")

        typer.echo("Sync complete!")
    finally:
        browser.close()
        repo.close()


def _detect_platform(url: str) -> str:
    if "douyin.com" in url:
        return "douyin"
    if "bilibili.com" in url or "b23.tv" in url:
        return "bilibili"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    raise typer.BadParameter(f"Unsupported platform URL: {url}")


def _extract_uid(url: str, platform: str) -> str:
    if platform == "douyin" and "/user/" in url:
        return url.split("/user/")[-1].split("/")[0].split("?")[0]
    if platform == "bilibili":
        parts = url.rstrip("/").split("/")
        return parts[-1].split("?")[0]
    if platform == "youtube":
        if "/@" in url:
            return url.split("/@")[-1].split("/")[0].split("?")[0]
        if "/channel/" in url:
            return url.split("/channel/")[-1].split("/")[0].split("?")[0]
    return hashlib.md5(url.encode()).hexdigest()[:12]


if __name__ == "__main__":
    app()