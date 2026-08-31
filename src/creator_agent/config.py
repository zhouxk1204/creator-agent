from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class BrowserSettings(BaseModel):
    user_data_dir: str = "./storage/.browser_profile"
    headless: bool = True


class CollectorSettings(BaseModel):
    scroll_delay_sec: list[float] = [1.0, 3.0]
    out_of_window_page_threshold: int = 2
    max_videos_per_sync: int = 50


class DownloaderSettings(BaseModel):
    timeout_sec: int = 120
    retry: int = 3


class AsrSettings(BaseModel):
    # FunASR runs in a DEDICATED conda env (not the main uv env), invoked via
    # subprocess. enabled=False by default so sync stays collect+download only.
    enabled: bool = False
    env_python: str = ""  # path to the dedicated env's python.exe
    worker_script: str = ""  # path to asr/worker.py (blank -> resolve from package)
    model: str = "paraformer-zh"
    vad_model: str = "fsmn-vad"
    punc_model: str = "ct-punc"
    device: str = "cuda:0"
    ffmpeg_path: str = ""  # blank -> shutil.which("ffmpeg")


class LogSettings(BaseModel):
    level: str = "INFO"
    file: str = "./logs/creator-agent.log"
    rotation: str = "10 MB"
    retention: str = "7 days"


class Settings(BaseSettings):
    storage_dir: str = "./storage"
    db_path: str = "./storage/creator_agent.db"
    browser: BrowserSettings = BrowserSettings()
    collector: CollectorSettings = CollectorSettings()
    downloader: DownloaderSettings = DownloaderSettings()
    asr: AsrSettings = AsrSettings()
    log: LogSettings = LogSettings()

    model_config = {"env_prefix": "CREATOR_AGENT_", "env_nested_delimiter": "__"}

    @classmethod
    def from_yaml(cls, path: str | Path = "config/settings.yaml") -> Settings:
        path = Path(path)
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)


def load_settings(path: str | Path = "config/settings.yaml") -> Settings:
    try:
        return Settings.from_yaml(path)
    except Exception:
        return Settings()
