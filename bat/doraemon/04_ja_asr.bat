@echo off
chcp 65001 >nul
call "%~dp0..\_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)
call .venv\Scripts\activate.bat >nul 2>&1

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only. cmd.exe mis-parses multibyte characters
rem under chcp 65001 and eats the rem/echo prefixes, which makes the window
rem die instantly. Chinese docs live in 04_ja_asr.md next to this file.
rem
rem Japanese ASR pipeline (six steps):
rem   1. extract audio       ffmpeg -> 16kHz mono WAV
rem   2. separate vocals     BS-RoFormer (audio-separator)
rem   3. recognize Japanese  Qwen3-ASR-1.7B (JA-Anime-Galgame fine-tune) over VAD speech chunks
rem   4. word-level align    Qwen3-ForcedAligner (separate conda env subprocess)
rem   5. boundary check      vs original video+audio: ffprobe duration check,
rem                          cue start/end snapped to real silence, suspicious
rem                          boundaries written to <video>.check.txt
rem   6. write Japanese SRT  <video>.srt (kept clean; import into JianYing
rem                          to check the timeline)
rem
rem Double-click (no args) = process every video in ja_asr.input_dir
rem (C:\test\temps): ASR + boundary check only, producing Japanese SRTs.
rem Pass your own args to override, e.g.:
rem   04_ja_asr.bat video.mp4                  only this video
rem   04_ja_asr.bat video.mp4 --force          ignore existing SRT/audio cache
rem   04_ja_asr.bat video.mp4 --translate      also translate to .zh.srt
rem   04_ja_asr.bat video.mp4 --translate --burn   then burn into .zh.mp4
rem
rem Tuning: config\settings.validate.yaml (auto-loaded when present) overrides
rem ONLY the ja_asr section of config\settings.yaml - edit one line, rerun with
rem --force and --out-dir per experiment. --config <file> uses a different one.
rem
rem Translation needs the local LLM server (llama.cpp). The CLI starts it
rem lazily right before the translate phase and stops it right after -- it
rem must NOT run during ASR: llama-server + torch/CUDA + vocal separator +
rem the ASR model together exceed the Windows commit limit, and the ASR
rem model load then dies with os error 1455 ("page file too small").
rem A server you started yourself (start_translate_server.bat) is left running.
rem ---------------------------------------------------------------------------

echo ========================================
echo   Japanese ASR (vocal separation + Qwen3-ASR)
echo   + boundary check vs original video/audio
echo ========================================
echo.
if "%~1"=="" (
    echo  Double-click = ASR + boundary check for every video in C:\test\temps\
    echo  Outputs      -^> vault episodes dir ^(ja_asr.out_dir in config\settings.yaml^)
    echo    ^<name^>.srt        Japanese subtitles ^(import into JianYing to check the timeline^)
    echo    ^<name^>.check.txt  suspicious-boundary report
    echo  Intermediates ^(mix + vocals + instrumental^) kept in C:\test\temps\_work.
    echo  Already-done videos are skipped automatically.
    echo  ^(Add --translate yourself if you also want .zh.srt.^)
) else (
    echo  Running: ja-asr %*
)
echo.
"%PY%" -m creator_agent.cli.main ja-asr %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Import the .srt into JianYing to check the timeline.
echo ========================================
pause >nul
exit /b %RC%
