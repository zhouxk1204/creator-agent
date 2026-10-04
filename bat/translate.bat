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
echo   JA -^> ZH subtitle translation (local LLM)
echo ========================================
echo.
echo  Double-click = translate every .srt under C:\test\temps\
echo  (already-translated ones are skipped; --force to redo).
echo  Or: translate.bat ^<video.mp4 ^| sub.srt^> [more ...] / drag files onto this file.
echo  Add --burn to also burn Chinese subs into ^<name^>.zh.mp4.
echo  Needs the local LLM server running (see translate.* in config\settings.yaml).
echo.
"%PY%" -m creator_agent.cli.main translate %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
