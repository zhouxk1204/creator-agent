@echo off
chcp 65001 >nul
cd /d "%~dp0.."
call .venv\Scripts\activate.bat >nul 2>&1
echo ========================================
echo   Douyin Download + Transcribe (ASR)
echo ========================================
echo.
echo  Usage: transcribe.bat [url]
echo  No arg = read Douyin link from clipboard.
echo.
.venv\Scripts\python.exe -m creator_agent.cli.main run %*
echo.
echo ========================================
echo  Done! Press any key to close.
echo ========================================
pause >nul
