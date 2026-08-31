"""ASR pipeline: extract audio -> FunASR transcript -> transcript.json + .txt.

The heavy FunASR model runs in a dedicated conda env (``creator-asr``), invoked
as a subprocess by :mod:`creator_agent.asr.transcriber`. This package's main-env
code only orchestrates: audio extraction (ffmpeg), plain-text file writing,
and status advancement. The main uv env takes no torch/funasr dependency.
"""
