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
echo   Learn from corrected subtitles
echo ========================================
echo.
echo  Reads subtitle-project\episodes\^<ep^>\01_ja + 02_ai + 03_final .srt,
echo  analyzes AI-vs-human diffs, updates knowledge\ memory and reports\.
echo  Next translation run injects the memory automatically.
echo  Needs the local LLM server running (translate.* in config\settings.yaml).
echo.
"%PY%" -m creator_agent.cli.main learn %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
