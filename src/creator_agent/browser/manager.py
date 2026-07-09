from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from pydantic import BaseModel


class BrowserConfig(BaseModel):
    user_data_dir: str | Path
    headless: bool = True
    channel: str = "chromium"
    viewport: tuple[int, int] = (1280, 720)
    locale: str = "zh-CN"


class BrowserManager:
    def __init__(self, config: BrowserConfig) -> None:
        self._config = config
        self._playwright = None
        self._context: BrowserContext | None = None
        self._browser: Browser | None = None

    def start(self) -> None:
        user_data_dir = Path(self._config.user_data_dir)
        user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=self._config.headless,
            viewport={"width": self._config.viewport[0], "height": self._config.viewport[1]},
            locale=self._config.locale,
            no_viewport=False,
        )
        self._context = self._browser

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("BrowserManager not started. Call start() first.")
        return self._context

    def new_page(self) -> Page:
        return self.context.new_page()

    def get_cookies(self, domain: str) -> list[dict]:
        return self.context.cookies(urls=[f"https://{domain}"])

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._browser = None
        self._playwright = None

    def __enter__(self) -> BrowserManager:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
