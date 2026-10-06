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
echo   Xiaohongshu Download + Transcribe (ASR)
echo ========================================
echo.
echo  Usage: xhs_download_transcribe.bat [url]
echo  No arg = read Xiaohongshu link from clipboard.
echo  (App: Share -^> Copy Link, then run this)
echo.
"%PY%" -m creator_agent.cli.main run %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
