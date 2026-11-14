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

echo ========================================
echo   Douyin covers - portrait 3:4 + landscape 4:3 (剪映 templates)
echo ========================================
echo.
echo  Input : metadata.json with zh.title + <story>_1.jpg in the materials dir
echo  Output: ^<story_id^>_douyin_portrait.jpg / _landscape.jpg + both publish.douyin paths
echo  Needs : Windows, 剪映 11.7, and "uv sync --extra covers" once.
echo.

"%PY%" scripts\make_douyin_covers.py %*
set "RC=%ERRORLEVEL%"

echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%

