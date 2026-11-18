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

rem --- Default input/output: the latest Doraemon materials folder. ----------
rem Explicit video arguments, --input-dir, and --out-dir/-o override defaults.
set "MATERIALS_STATE=%REPO_ROOT%\logs\.last_doraemon_materials"
if not exist "%MATERIALS_STATE%" (
    echo Missing materials state: "%MATERIALS_STATE%"
    echo Run the latest-episode materials launcher first.
    pause >nul
    exit /b 1
)
set "MATERIALS_DIR="
set /p "MATERIALS_DIR="<"%MATERIALS_STATE%"
if not defined MATERIALS_DIR (
    echo Materials state is empty: "%MATERIALS_STATE%"
    pause >nul
    exit /b 1
)
if not exist "%MATERIALS_DIR%\" (
    echo Materials folder not found: "%MATERIALS_DIR%"
    pause >nul
    exit /b 1
)

set "OUTDIR=%MATERIALS_DIR%"
set "ARGS=%*"
set "HAS_INPUT_DIR="
set "HAS_OUT_DIR="
if not "%~1"=="" (
    for %%A in (%*) do (
        if /i "%%~A"=="--input-dir" set "HAS_INPUT_DIR=1"
        if /i "%%~A"=="--out-dir" set "HAS_OUT_DIR=1"
        if /i "%%~A"=="-o" set "HAS_OUT_DIR=1"
    )
)
if not defined HAS_INPUT_DIR set "ARGS=--input-dir "%MATERIALS_DIR%" %ARGS%"
if not defined HAS_OUT_DIR set "ARGS=%ARGS% --out-dir "%OUTDIR%""

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
rem Double-click (no args) = recursively process every video in the folder
rem recorded by logs\.last_doraemon_materials. The same folder is the output
rem root, with one <name> subfolder per video.
rem Pass your own args to override, e.g.:
rem   04_ja_asr.bat video.mp4                  only this video
rem   04_ja_asr.bat video.mp4 --force          ignore existing SRT/audio cache
rem   04_ja_asr.bat video.mp4 --translate      also translate to .zh.srt
rem   04_ja_asr.bat video.mp4 --translate --burn   then burn into .zh.mp4
rem   04_ja_asr.bat video.mp4 --out-dir D:\somewhere   override the saved output folder
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
    echo  Input/output -^> %MATERIALS_DIR%
    echo    ^<name^>.alignment.^<run^>.srt  Raw alignment inspection ^(open this first^)
    echo    ^<name^>.srt        Final Japanese subtitles
    echo    ^<name^>.check.txt  diagnostic-boundary report
    echo    ^<name^>.timing.^<run^>.json    Raw ASR and local/global word times
    echo  Intermediates ^(mix + vocals + instrumental^) kept in C:\test\temps\_work.
    echo  Already-done videos are skipped automatically.
    echo  ^(Add --translate yourself if you also want .zh.srt.^)
) else (
    echo  Running: ja-asr %ARGS%
)
echo.
"%PY%" -m creator_agent.cli.main ja-asr %ARGS%
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Open the .alignment.^<run^>.srt first to inspect raw alignment.
echo ========================================
pause >nul
exit /b %RC%
