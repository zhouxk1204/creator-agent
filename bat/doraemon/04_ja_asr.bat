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
rem Double-click (no args) = Japanese ASR -> JA .srt -> translate -> ZH .srt.
rem (No burn by default; add --burn yourself if you want a hardsubbed mp4.)
rem Pass your own args to override, e.g.:
rem   04_ja_asr.bat video.mp4                  (ASR only, JA .srt)
rem   04_ja_asr.bat video.mp4 --burn           (also burn subs into .zh.mp4)
rem ---------------------------------------------------------------------------
rem Translation needs the local LLM server (llama.cpp). The CLI starts it
rem lazily right before the translate phase and stops it right after — it must
rem NOT run during ASR: llama-server + torch/CUDA + vocal separator + the ASR
rem model together exceed the Windows commit limit, and the ASR model load then
rem dies with os error 1455 ("page file too small"). A server you started
rem yourself (start_translate_server.bat) is left running.
set "ARGS=%*"
if "%~1"=="" set "ARGS=--translate"

echo ========================================
echo   Japanese ASR (vocal separation + Qwen3-ASR)
if "%~1"=="" echo   + translate  [JA srt + ZH srt]
echo ========================================
echo.
if "%~1"=="" (
    echo  Double-click = ASR + translate every video in C:\test\temps\
    echo  Subtitles -^> vault episodes dir ^(ja_asr.out_dir in config\settings.yaml^).
    echo  Intermediates ^(mix + vocals + instrumental^) kept in C:\test\temps\_work.
    echo  Already-done videos are skipped automatically.
) else (
    echo  Running: ja-asr %ARGS%
)
echo.
"%PY%" -m creator_agent.cli.main ja-asr %ARGS%
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
