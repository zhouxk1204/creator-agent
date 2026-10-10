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
echo   Character Frame Collection
echo ========================================
echo.
if "%~1"=="" (
    echo  Usage:
    echo    collect_character_frames.bat --episode 935_1 --preview
    echo    collect_character_frames.bat --episode 935_1
    echo    collect_character_frames.bat --episode 935_1 --sync-labels
    echo    collect_character_frames.bat --scenes-json C:/path/scenes.json --video C:/path/video.mp4
    echo.
    echo  Workflow: preview -^> extract -^> classify into characters\^<char^>\ -^> --sync-labels
    echo  Full guide: docs\character_collection_guide.md
    echo.
    pause >nul
    exit /b 0
)
"%PY%" scripts\collect_character_frames.py %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done!
echo ========================================
pause >nul
exit /b %RC%
