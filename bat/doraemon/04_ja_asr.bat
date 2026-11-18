@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
call "%~dp0..\_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)
call .venv\Scripts\activate.bat >nul 2>&1

rem --- Default input and output: the last downloaded materials folder. --------
rem Explicit video paths, --input-dir, and --out-dir later in %* still win.
set "MATERIALS_STATE=%REPO_ROOT%\logs\.last_doraemon_materials"
if not exist "%MATERIALS_STATE%" (
    echo.
    echo [X] Saved materials folder not found: "%MATERIALS_STATE%"
    echo     Run the latest-materials downloader first.
    pause >nul
    exit /b 1
)
set "MATERIALS_DIR="
for /f "usebackq delims=" %%I in ("%MATERIALS_STATE%") do if not defined MATERIALS_DIR set "MATERIALS_DIR=%%I"
if not defined MATERIALS_DIR (
    echo.
    echo [X] Saved materials folder is empty: "%MATERIALS_STATE%"
    pause >nul
    exit /b 1
)
if not exist "%MATERIALS_DIR%\" (
    echo.
    echo [X] Saved materials folder no longer exists: "%MATERIALS_DIR%"
    pause >nul
    exit /b 1
)

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
rem                          diagnostic only; cue timestamps are not snapped.
rem                          Suspects are written to <video>.check.txt.
rem   6. write Japanese SRT  <video>.srt (kept clean; import into JianYing
rem                          to check the timeline)
rem
rem Double-click (no args) = process every video in the folder saved in
rem logs\.last_doraemon_materials. Input and output roots are that same folder;
rem each video's files go into <materials>\<name>\.
rem Pass your own args to override, e.g.:
rem   04_ja_asr.bat video.mp4                  only this video
rem   04_ja_asr.bat video.mp4 --force          ignore existing SRT/audio cache
rem   04_ja_asr.bat video.mp4 --translate      also translate to .zh.srt
rem   04_ja_asr.bat video.mp4 --translate --burn   then burn into .zh.mp4
rem   04_ja_asr.bat --input-dir D:\videos --out-dir D:\results
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
    echo  Double-click = ASR + boundary check for every video in:
    echo    %MATERIALS_DIR%
    echo  Input/output root: the saved materials folder above.
    echo    ^<name^>\^<name^>.alignment.^<run^>.srt  Raw alignment inspection ^(open this first^)
    echo    ^<name^>\^<name^>.srt        Final Japanese subtitles
    echo    ^<name^>\^<name^>.check.txt  diagnostic-boundary report
    echo    ^<name^>\^<name^>.timing.^<run^>.json    Raw ASR and local/global word times
    echo  Already-done videos are skipped automatically.
    echo  ^(Add --translate yourself if you also want .zh.srt.^)
) else (
    echo  Saved materials folder: %MATERIALS_DIR%
    echo  Running: ja-asr %*
)
echo.
"%PY%" -m creator_agent.cli.main ja-asr --input-dir "%MATERIALS_DIR%" --out-dir "%MATERIALS_DIR%" %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Open the .alignment.^<run^>.srt first to inspect raw alignment.
echo ========================================
pause >nul
exit /b %RC%
