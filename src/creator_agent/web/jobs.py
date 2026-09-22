"""Background job runner for the web UI's "paste a URL" box.

One job at a time (the pipeline is browser- and ASR-heavy; parallel runs would
contend on the single browser profile). The worker thread builds its own
components - a fresh ``Repository`` (sqlite connections are per-thread),
``BrowserManager`` (Playwright's sync API must start/stop in the thread that
uses it) - runs :meth:`PipelineRunner.sync_video_url`, and tears everything
down. Status is kept in memory only; refresh the page freely, a crashed or
interrupted job just disappears.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from creator_agent.asr.transcriber import Transcriber
from creator_agent.browser.manager import BrowserConfig, BrowserManager
from creator_agent.config import Settings
from creator_agent.downloader.downloader import Downloader
from creator_agent.pipeline.runner import PipelineRunner, SingleVideoResult
from creator_agent.repository.sqlite_repo import Repository
from creator_agent.storage.file_storage import FileStorage

logger = logging.getLogger(__name__)


class RunJobManager:
    """Serial, in-memory queue (depth 1) for single-URL pipeline runs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._job: dict | None = None

    def submit(self, url: str) -> tuple[bool, str]:
        """Start a run for ``url``. Returns ``(accepted, message)``."""
        url = (url or "").strip()
        if not url:
            return False, "链接为空"
        with self._lock:
            if self._job and not self._job.get("done"):
                return False, f"已有任务在运行: {self._job.get('url', '')[:80]}"
            self._job = {
                "url": url,
                "stages": [],
                "started_at": time.time(),
                "done": False,
                "error": None,
                "video_id": None,
                "title": "",
                "transcribed": False,
            }
            thread = threading.Thread(target=self._work, args=(url,), daemon=True, name="run-job")
            thread.start()
        return True, "已提交"

    def snapshot(self) -> dict | None:
        """A copy of the current/last job for rendering."""
        with self._lock:
            return dict(self._job) if self._job else None

    # -- worker ---------------------------------------------------------------

    def _set_stage(self, msg: str) -> None:
        with self._lock:
            if self._job is not None:
                self._job["stages"].append(msg)

    def _finish(self, **fields) -> None:
        with self._lock:
            if self._job is not None:
                self._job.update(fields)
                self._job["done"] = True

    def _work(self, url: str) -> None:
        settings = self._settings
        repo: Repository | None = None
        browser: BrowserManager | None = None
        try:
            repo = Repository(settings.db_path)
            storage = FileStorage(settings.storage_dir)
            browser = BrowserManager(
                BrowserConfig(user_data_dir=settings.browser.user_data_dir, headless=settings.browser.headless)
            )
            downloader = Downloader(
                storage=storage,
                browser=browser,
                timeout_sec=settings.downloader.timeout_sec,
                retries=settings.downloader.retry,
            )
            transcriber: Transcriber | None = None
            if settings.asr.env_python and Path(settings.asr.env_python).exists():
                transcriber = Transcriber(storage=storage, settings=settings.asr, repo=repo)
            runner = PipelineRunner(
                repo=repo,
                storage=storage,
                browser=browser,
                downloader=downloader,
                transcriber=transcriber,
            )
            browser.start()
            result: SingleVideoResult = runner.sync_video_url(
                url,
                do_asr=transcriber is not None,
                timeout_sec=settings.downloader.timeout_sec,
                progress=self._set_stage,
            )
            self._finish(
                error=result.error,
                video_id=result.video_id or None,
                title=result.title,
                transcribed=result.transcribed,
            )
        except Exception as e:
            logger.exception("run job failed for %r", url)
            self._finish(error=str(e))
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if repo is not None:
                repo.close()
