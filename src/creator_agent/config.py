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
    # Default output dir for .srt/.txt/.transcript.json — one <stem>/ subdir
    # per video inside it; blank = next to each video.
    out_dir: str = ""
    # Intermediates (extracted mix WAV + separated vocal/instrumental stems)
    # are kept here per video when set, so separation quality can be audited;
    # blank = everything intermediate is discarded after the run.
    work_dir: str = ""
    model: str = "Qwen/Qwen3-ASR-1.7B-hf"  # or Qwen/Qwen3-ASR-0.6B-hf for speed
    sep_model: str = ""  # audio-separator model filename; blank = default BS-RoFormer
    device: str = "cuda:0"  # "mps" / "cpu" for Mac debugging
    language: str = "Japanese"
    keep_vocals: bool = False  # keep isolated vocals.wav into out_dir (superseded by work_dir)
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
    pause_sec: float = 0.6  # hard cue break at silence gaps of at least this (needs word alignment)
    # VAD (silero) — low min-speech keeps short interjections (「あっ」「えっ」),
    # speech-pad keeps soft onsets from being clipped.
    vad_min_speech_ms: int = 120
    vad_min_silence_ms: int = 300
    vad_speech_pad_ms: int = 150
    # --- 实验开关（默认全部保持旧行为，方便和 baseline 逐项对比）---
    # Permissive-pass VAD probability threshold (silero default 0.5). Lower it
    # (e.g. 0.35) to recover weak/soft speech the VAD used to miss — at the
    # cost of more music/noise false positives. The tight audit pass stays at
    # 0.5 (it is the measuring baseline; never change both at once).
    vad_threshold: float = 0.5
    # Recovery pass: tight-VAD speech no successful ASR chunk covers gets one
    # more ASR+alignment attempt as extra chunks (reason="recovered"), reusing
    # the same aligner subprocess (no second model load).
    recover_missed: bool = False
    recover_min_sec: float = 0.4  # missed ranges shorter than this are not recovered
    # Repeat-tail hallucination guard by speaking rate: >0 enables the cps
    # criterion — a repeat tail is only cut when chars/sec over the chunk's
    # speech duration exceeds this (12.0 is a good trial value); real
    # 「うんうんうん」-style back-channels at normal speed are kept. 0 = old
    # behavior (every repeat tail is cut).
    repeat_tail_max_cps: float = 0.0
    # .srt-only display-time polish (transcript.json keeps real speech times).
    # All 0 = off. min_display stretches too-short cues for readability;
    # lead/tail add pre/post margins — keep them 0 when boundary_check is on
    # (it already adds lead_in/tail_out when snapping; enabling both would
    # double the margin).
    srt_min_display_sec: float = 0.0
    srt_lead_in_sec: float = 0.0
    srt_tail_sec: float = 0.0
    # Chunking: aim for 3-8s chunks; max_chunk_sec is the HARD cap (oversized
    # chunks are force-split at the nearest natural pause, else hard-cut).
    chunk_target_min_sec: float = 3.0
    chunk_target_max_sec: float = 8.0
    max_chunk_sec: float = 15.0
    asr_max_new_tokens: int = 1024  # ASR generation cap (512 could truncate long chunks)
    # Alignment quality gate: window padding per side (also the max distance a
    # word may spill outside its chunk) and the word-coverage floor.
    align_pad_sec: float = 0.3
    align_min_coverage: float = 0.5
    # Boundary check against the ORIGINAL video+audio (post-ASR, main env):
    # snap cue edges to true silences (ffmpeg silencedetect on the vocal stem)
    # and flag suspects into <stem>.check.txt. The .srt stays clean.
    boundary_check: bool = True
    boundary_snap_sec: float = 0.5  # max distance a cue edge may move to reach silence
    boundary_silence_db: float = -35.0  # silencedetect noise floor
    boundary_min_silence_sec: float = 0.12  # silencedetect min silence duration
    boundary_lead_in_sec: float = 0.08  # subtitle appears this far before speech onset
    boundary_tail_sec: float = 0.10  # ... and lingers this far after speech offset
    # <stem>.review.<timestamp>.json: srt-aligned cues + pipeline diagnostics
    # + empty question/expected fields to annotate while listening (see
    # docs/subtitle-issue-template.md). Every run writes a NEW timestamped
    # file; previous runs' files (with any hand-filled annotations) are kept.
    review_json: bool = True
    verbose: bool = False  # debug logging in the worker (VAD segments, word times, cues)


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
