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
echo   Doraemon latest-episode materials
echo   fetch -^> TVer -^> split -^> upscale
echo ========================================
echo.
echo  Enter = latest aired episode, full pipeline.
echo  Or type an episode number for story info only.
echo  Synopsis translation runs later in 06_correct_subtitles.bat.
echo.
set ep=%~1
if "%ep%"=="" set /p ep="Episode number (e.g. 934, Enter = latest aired): "

set "RC=0"
set "MAT="
if not "%ep%"=="" goto :story_only

"%PY%" scripts\fetch_doraemon.py --latest
if errorlevel 1 ( set "RC=1" & goto :done )

if not exist "%REPO_ROOT%\logs\.last_fetched" (
    echo State file .last_fetched missing - cannot continue.
    set "RC=1"
    goto :done
)
set /p ep=<"%REPO_ROOT%\logs\.last_fetched"
set "airdate="
if exist "%REPO_ROOT%\logs\.last_fetched_date" set /p airdate=<"%REPO_ROOT%\logs\.last_fetched_date"

echo.
echo ========================================
echo   Episode %ep% (aired %airdate%) - full pipeline
echo ========================================

set "MAT=%USERPROFILE%\Desktop\doraemon_%ep%"
if not exist "%MAT%" mkdir "%MAT%"
if not exist "%MAT%\" (
    echo [materials] Failed to create folder: "%MAT%"
    set "RC=1"
    goto :done
)
echo [materials] "%MAT%"
"%PY%" -c "import os, sys; from pathlib import Path; target = Path(sys.argv[1]); temp = target.with_name(target.name + '.tmp'); temp.write_text(sys.argv[2] + '\n', encoding='utf-8'); os.replace(temp, target)" "%REPO_ROOT%\logs\.last_doraemon_materials" "%MAT%"
if errorlevel 1 ( set "RC=1" & goto :done )

set "DATEARG="
if not "%airdate%"=="" set "DATEARG=--broadcast-date %airdate%"
"%PY%" scripts\download_tver.py --out-dir "%MAT%" --name %ep% %DATEARG%
if errorlevel 1 ( set "RC=1" & goto :done )

"%PY%" scripts\split_episode.py "%MAT%\%ep%.mp4" -o "%MAT%" --confirm-anomaly
set "RC=%ERRORLEVEL%"
if "%RC%"=="2" (
    echo  Split cancelled - preview folder: %MAT%\preview
    set "RC=0"
    goto :done
)
if not "%RC%"=="0" goto :done

for %%I in (1 2) do (
    if not exist "%MAT%\%ep%_%%I.mp4" (
        echo [upscale] Missing split clip: %MAT%\%ep%_%%I.mp4
        set "RC=1"
    )
)
if not "%RC%"=="0" goto :done

if not exist "C:\test\temps" mkdir "C:\test\temps"
for %%I in (1 2) do (
    copy /Y "%MAT%\%ep%_%%I.mp4" "C:\test\temps\" >nul
    if errorlevel 1 (
        echo [upscale] Failed to copy %ep%_%%I.mp4 to C:\test\temps
        set "RC=1"
    )
)
if not "%RC%"=="0" goto :done

echo [upscale] Running Real-ESRGAN on %ep%_1.mp4 and %ep%_2.mp4...
call "%~dp0run_upscale.bat"
if errorlevel 1 (
    echo [upscale] Failed - see C:\test\run_log.txt
    set "RC=1"
    goto :done
)
echo [upscale] Outputs: C:\test\%ep%_1_4K.mp4 and C:\test\%ep%_2_4K.mp4
goto :done

:story_only
"%PY%" scripts\fetch_doraemon.py %ep%
set "RC=%ERRORLEVEL%"

:done
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" (
    echo  Done!
    if not "%MAT%"=="" (
        echo  Materials: %MAT%
        explorer "%MAT%"
    )
)
echo ========================================
pause >nul
exit /b %RC%
