@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
call "%~dp0..\_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)
call .venv\Scripts\activate.bat >nul 2>&1

rem ---------------------------------------------------------------------------
rem Latest-episode Doraemon materials pipeline.
rem
rem   double-click / no number: FULL pipeline for the latest aired episode -
rem       fetch story info -> translate -> TVer download (<ep>.mp4) ->
rem       split (cuts directly; anomalies pop a contact sheet for y/n)
rem   with a number (e.g. 934): story info + translation only - no download,
rem       no split
rem
rem All materials for the episode land in one folder:
rem   Desktop\doraemon_<ep>\   story.md, metadata.json, story images, covers,
rem                            <ep>.mp4 (+ thumbnail/info-json), split clips
rem                            (<ep>_1.mp4 ...), <ep>_split.json, preview\
rem
rem The TVer download is matched by broadcast date (--broadcast-date), so if
rem TVer has not posted the latest episode yet the run stops at the download
rem step instead of saving the wrong episode under the new number.
rem ---------------------------------------------------------------------------

echo ========================================
echo   Doraemon latest-episode materials
echo   fetch -^> translate -^> TVer -^> split
echo ========================================
echo.
echo  Enter = latest aired episode, full pipeline.
echo  Or type an episode number for story info only.
echo.
set ep=%~1
if "%ep%"=="" set /p ep="Episode number (e.g. 934, Enter = latest aired): "

set "RC=0"
set "MAT="
set "STARTED_SERVER=0"
if not "%ep%"=="" goto :story_only

rem ===========================================================================
rem  Full pipeline: latest story -> translate -> TVer download -> split
rem ===========================================================================
"%PY%" scripts\fetch_doraemon.py --latest
if errorlevel 1 ( set "RC=1" & goto :done )

if not exist "%REPO_ROOT%\storage\doraemon\.last_fetched" (
    echo State file .last_fetched missing - cannot continue.
    set "RC=1"
    goto :done
)
set /p ep=<"%REPO_ROOT%\storage\doraemon\.last_fetched"
set "airdate="
if exist "%REPO_ROOT%\storage\doraemon\.last_fetched_date" set /p airdate=<"%REPO_ROOT%\storage\doraemon\.last_fetched_date"

echo.
echo ========================================
echo   Episode %ep% (aired %airdate%) - full pipeline
echo ========================================

rem --- translation (non-fatal: the pipeline continues without it) ---
call :ensure_server
if errorlevel 1 (
    echo [translate] LLM server unavailable - skipping translation.
) else (
    "%PY%" scripts\translate_doraemon.py %ep%
    if errorlevel 1 echo [translate] failed - continuing pipeline.
)

rem --- materials folder: Desktop\doraemon_<ep> ---
rem Already populated by fetch_doraemon.py (story.md, metadata.json, images,
rem covers) and translate_doraemon.py (translated {ep}_{i}_zh.md notes with
rem tags); the download + split steps below add the video to the SAME folder.
set "MAT=%USERPROFILE%\Desktop\doraemon_%ep%"
if not exist "%MAT%" mkdir "%MAT%"
echo [materials] %MAT%

rem --- download the TVer episode aired on the story date as <ep>.mp4 ---
set "DATEARG="
if not "%airdate%"=="" set "DATEARG=--broadcast-date %airdate%"
"%PY%" scripts\download_tver.py --out-dir "%MAT%" --name %ep% %DATEARG%
if errorlevel 1 ( set "RC=1" & goto :done )

rem --- split: cuts directly; only anomalies (card count / end snap / story
rem     length outside 8~13 min) pop the contact sheet for a y/n (n -> RC 2) ---
"%PY%" scripts\split_episode.py "%MAT%\%ep%.mp4" -o "%MAT%" --confirm-anomaly
set "RC=%ERRORLEVEL%"
if "%RC%"=="2" (
    echo  Split cancelled - preview folder: %MAT%\preview
    set "RC=0"
)
goto :done

rem ===========================================================================
:story_only
rem Explicit episode: story info + translation only (no download / split).
rem ===========================================================================
"%PY%" scripts\fetch_doraemon.py %ep%
if errorlevel 1 ( set "RC=1" & goto :done )
call :ensure_server
if errorlevel 1 ( set "RC=1" & goto :done )
"%PY%" scripts\translate_doraemon.py %ep%
set "RC=%ERRORLEVEL%"

:done
if "%STARTED_SERVER%"=="1" (
    echo.
    echo [server] stopping translation LLM to free VRAM/RAM...
    taskkill /IM llama-server.exe /F >nul 2>&1
)
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

rem ---------------------------------------------------------------------------
:ensure_server
rem NOTE: must be -sf (fail on HTTP error) against /health - llama-server
rem returns 503 on /health while the model is still loading, and /v1/models
rem answers 200 the moment the HTTP listener is up, long before inference works.
curl -sf -m 3 http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 (
    echo [server] translation LLM already running.
    exit /b 0
)
echo [server] starting translation LLM ^(llama.cpp^) in a separate window...
start "translate-server" /min "%~dp0..\start_translate_server.bat"
set "STARTED_SERVER=1"
set /a TRIES=0
:wait_server
curl -sf -m 3 http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 (
    echo [server] ready.
    exit /b 0
)
set /a TRIES+=1
if %TRIES% GEQ 60 (
    echo [server] did not become ready in time.
    taskkill /IM llama-server.exe /F >nul 2>&1
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto :wait_server
