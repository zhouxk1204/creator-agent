@echo off
chcp 65001 >nul
call "%~dp0_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)
call .venv\Scripts\activate.bat >nul 2>&1
echo ========================================
echo   Japanese ASR (vocal separation + Qwen3-ASR)
echo ========================================
echo.
echo  Double-click = process every video in storage\ja_inbox\
echo  (already-transcribed ones are skipped automatically).
echo  Or: ja-asr.bat ^<video.mp4^> [more.mp4 ...]  /  drag mp4 files onto this file.
echo  Writes ^<name^>.txt / .srt / .transcript.json next to each video.
echo  Needs the creator-asr-ja env (ja_asr.env_python in config\settings.yaml).
echo.
"%PY%" -m creator_agent.cli.main ja-asr %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
