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
echo   Download + Transcribe (Douyin / Xiaohongshu)
echo ========================================
echo.
echo  Platform is detected from the link automatically.
echo  Full share text works too (the link is pulled out of it).
echo.
set "url=%~1"
if "%url%"=="" set /p url="Paste link here (Enter = use clipboard): "
if "%url%"=="" (
    "%PY%" -m creator_agent.cli.main run
) else (
    "%PY%" -m creator_agent.cli.main run "%url%"
)
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
