@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
REM Video stages enabled by default; set to 0 for materials-only mode.
set "DOWNLOAD_VIDEO=1"
REM Upscale (3/3) is enabled; set to 0 to check split clips without Real-ESRGAN.
set "UPSCALE_VIDEO=1"
REM Always refresh download/upscale progress, including piped child output.
set "CREATOR_AGENT_LIVE_PROGRESS=1"
REM Splitting (2/3) locates each story end with the reference card stills
REM assets\end1.jpg / end2.jpg first (prints the similarity and cuts on the
REM matched frame), and only falls back to black frames; --no-end-ref disables it.
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
if not "%DOWNLOAD_VIDEO%"=="0" if "%UPSCALE_VIDEO%"=="0" echo   TVer -^> split ^(upscale OFF^) ^+ parallel materials
if not "%DOWNLOAD_VIDEO%"=="0" if not "%UPSCALE_VIDEO%"=="0" echo   TVer -^> split -^> upscale ^+ parallel materials
echo ========================================
echo.
echo  Enter = resume unfinished task, otherwise latest aired episode.
echo  DOWNLOAD_VIDEO=%DOWNLOAD_VIDEO% ^(0 = materials only, 1 = full pipeline^)
echo  UPSCALE_VIDEO=%UPSCALE_VIDEO% ^(0 = split clips only, 1 = also Real-ESRGAN^)
echo  Or type an episode number for story info only.
echo  --latest = start latest instead of resuming a saved task.
echo  Synopsis translation runs later in bat\doraemon_legacy\06_correct_subtitles.bat.
echo.
set "ep=%~1"
set "LATESTARG="
set "RC=0"
set "MAT="
if "%ep%"=="" set /p ep="Episode number (Enter = resume/latest): "
if "%ep%"=="--latest" (
    set "LATESTARG=--latest"
    goto :latest
)
if not "%ep%"=="" goto :story_only

:latest
set "VIDEOARG="
if "%DOWNLOAD_VIDEO%"=="0" set "VIDEOARG=--no-video"
"%PY%" scripts\download_doraemon_materials.py %VIDEOARG% %LATESTARG%
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
