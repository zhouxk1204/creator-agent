@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
REM Video stages enabled by default; set to 0 for materials-only mode.
set "DOWNLOAD_VIDEO=1"
call "%~dp0..\_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)
call .venv\Scripts\activate.bat >nul 2>&1

echo ========================================
echo   Doraemon latest-episode materials
if "%DOWNLOAD_VIDEO%"=="0" echo   materials only ^(video disabled^)
if not "%DOWNLOAD_VIDEO%"=="0" echo   TVer -^> split -^> upscale ^+ parallel materials
echo ========================================
echo.
echo  Enter = latest aired episode.
echo  DOWNLOAD_VIDEO=%DOWNLOAD_VIDEO% ^(0 = materials only, 1 = full pipeline^)
echo  Or type an episode number for story info only.
echo  Synopsis translation runs later in 06_correct_subtitles.bat.
echo.
set "ep=%~1"
if "%ep%"=="" set /p ep="Episode number (e.g. 934, Enter = latest aired): "

set "RC=0"
set "MAT="
if not "%ep%"=="" goto :story_only

set "VIDEOARG="
if "%DOWNLOAD_VIDEO%"=="0" set "VIDEOARG=--no-video"
"%PY%" scripts\download_doraemon_materials.py %VIDEOARG%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :done
if exist "%REPO_ROOT%\logs\.last_doraemon_materials" set /p MAT=<"%REPO_ROOT%\logs\.last_doraemon_materials"
goto :done

:story_only
"%PY%" scripts\fetch_doraemon.py "%ep%"
set "RC=%ERRORLEVEL%"

:done
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" (
    echo  Done! Check any materials warnings above.
    if not "%MAT%"=="" (
        echo  Materials: %MAT%
        explorer "%MAT%"
    )
)
echo ========================================
pause >nul
exit /b %RC%
