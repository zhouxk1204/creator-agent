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


class JaAsrSettings(BaseModel):
    # Japanese ASR (vocal separation + Qwen3-ASR) runs in a DEDICATED conda env
    # (creator-asr-ja), invoked via subprocess — transformers>=5.13 may conflict
    # with the FunASR env, so keep them separate. File-based tool, offline.
    env_python: str = ""  # path to the creator-asr-ja env's python
    input_dir: str = "./storage/ja_inbox"  # drop videos here; `ja-asr` with no args scans it
    model: str = "Qwen/Qwen3-ASR-1.7B-hf"  # or Qwen/Qwen3-ASR-0.6B-hf for speed
    sep_model: str = ""  # audio-separator model filename; blank = default BS-RoFormer
    device: str = "cuda:0"  # "mps" / "cpu" for Mac debugging
    language: str = "Japanese"
    keep_vocals: bool = False  # keep isolated vocals.wav (only with --out-dir)
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
    ja_asr: JaAsrSettings = JaAsrSettings()
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
