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
    input_dir: str = "C:/test/temps"  # drop videos here; `ja-asr` with no args scans it
    model: str = "Qwen/Qwen3-ASR-1.7B-hf"  # or Qwen/Qwen3-ASR-0.6B-hf for speed
    sep_model: str = ""  # audio-separator model filename; blank = default BS-RoFormer
    device: str = "cuda:0"  # "mps" / "cpu" for Mac debugging
    language: str = "Japanese"
    keep_vocals: bool = False  # keep isolated vocals.wav (only with --out-dir)
    ffmpeg_path: str = ""  # blank -> shutil.which("ffmpeg")
    # Word-level alignment (Qwen3-ForcedAligner-0.6B, HF id or local path).
    # Runs in a SEPARATE env (aligner_env_python) because qwen-asr pins
    # transformers==4.57.6 while the ASR model needs >=5.13. Both aligner_model
    # AND aligner_env_python must be set; otherwise cue times fall back to
    # proportional allocation.
    aligner_model: str = ""
    aligner_env_python: str = ""  # path to the creator-asr-ja-aligner env's python
    # Speaker diarization (ModelScope CAM++ id, e.g.
    # iic/speech_campplus_sv_zh-cn_16k-common). Blank = skip diarization.
    speaker_model: str = ""
    speaker_threshold: float = 0.5  # cosine distance for clustering; lower = more speakers
    # Subtitle cue shaping.
    max_cue_chars: int = 24  # split/merge cues to at most this many characters
    max_cue_sec: float = 8.0  # ... and at most this duration
    max_chunk_sec: float = 15.0  # ASR chunk cap (was hardcoded 30s)


class TranslateSettings(BaseModel):
    # JA -> ZH subtitle translation via a local OpenAI-compatible LLM server
    # (Ollama / llama.cpp / vLLM / LM Studio). No heavy deps in this env.
    base_url: str = "http://localhost:11434/v1"  # Ollama default
    api_key: str = "ollama"
    model: str = "qwen3.5:9b"  # whatever tag the installed Qwen3.5 9B has
    batch_size: int = 30  # cues per LLM call (keep within 20~50)
    context_cues: int = 5  # preceding untranslated cues sent as context
    timeout_sec: float = 300
    # Subtitle burn-in (ffmpeg subtitles filter, needs libass).
    ffmpeg_path: str = ""  # blank -> shutil.which("ffmpeg")
    burn_font: str = "Microsoft YaHei"
    burn_fontsize: int = 16
    # Learning from human corrections (see `creator-agent learn`). Layout:
    # <project_dir>/{episodes/<ep>/{01_original_ja,02_ai_zh,03_final_zh}.srt,
    #               knowledge/*.json+md, reports/*}
    project_dir: str = "./subtitle-project"
    memory_cases: int = 10  # max similar past cases injected per translation batch
    # Vault integration: <project_dir>/简介/<stem>.md is auto-injected into the
    # translation prompt as story context when it exists; export_subtitles
    # mirrors generated <stem>.srt/.zh.srt into <project_dir>/<stem>/
    # (copy-if-newer, so Obsidian hand-edits survive).
    export_subtitles: bool = False


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
    translate: TranslateSettings = TranslateSettings()
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
