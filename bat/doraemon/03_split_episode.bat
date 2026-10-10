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
rem Episode splitter: detect title cards -> cut directly (no confirmation).
rem
rem   03_split_episode.bat                          prompts for the video path
rem   03_split_episode.bat C:\test\935.mp4          path given directly (or drag & drop)
rem   03_split_episode.bat C:\test                  a directory: uses the FIRST video in it
rem   03_split_episode.bat C:\test --copy           extra args are passed through
rem
rem Flow: split_episode.py detects title cards and cuts in one pass. Story #1
rem starts at the FIRST title card (the OP/intro before it is discarded;
rem --keep-intro keeps it). Output files are named from the episode titles
rem embedded in the source filename's Japanese corner brackets
rem ( doraemon [A][B].mp4 -> A.mp4 / B.mp4 ), plus <stem>_split.json.
rem If detection looks wrong (>3 episodes) the script refuses to cut and
rem auto-saves preview frames to output\preview\; inspect them, then rerun
rem with --episodes N to force the real story count.
rem ---------------------------------------------------------------------------

set "VIDEO=%~1"
shift
set "EXTRA="
:collect_args
if "%~1"=="" goto :args_done
set "EXTRA=%EXTRA% %~1"
shift
goto :collect_args
:args_done

if not "%VIDEO%"=="" goto :have_video
echo ========================================
echo   Episode splitter (title card detect + cut)
echo ========================================
echo.
echo  Drag a video file OR a folder into this window and press Enter,
echo  or paste a full path, e.g.  C:\test  or  C:\test\935.mp4
echo  (a folder processes its first video automatically)
echo.
set /p "VIDEO=Video path or folder: "
rem strip surrounding quotes from a pasted path
set "VIDEO=%VIDEO:"=%"

:have_video
if "%VIDEO%"=="" (
    echo  No video given, aborting.
    pause >nul
    exit /b 1
)
rem If a directory was given, pick the first video file in it (by name).
if exist "%VIDEO%\*" (
    set "FOUND="
    for %%E in (mp4 mkv ts mov avi flv webm) do (
        if not defined FOUND (
            for /f "delims=" %%F in ('dir /b /o:n "%VIDEO%\*.%%E" 2^>nul') do (
                if not defined FOUND set "FOUND=%VIDEO%\%%F"
            )
        )
    )
    if not defined FOUND (
        echo  No video found in folder: %VIDEO%
        pause >nul
        exit /b 1
    )
    set "VIDEO=!FOUND!"
    echo  Using first video in folder: !VIDEO!
)
if not exist "%VIDEO%" (
    echo  File not found: %VIDEO%
    pause >nul
    exit /b 1
)

echo.
echo ========================================
echo   Splitting: %VIDEO%
echo ========================================
echo.
"%PY%" scripts\split_episode.py "%VIDEO%"%EXTRA%
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" (
    echo  FAILED ^(exit code %RC%^) - see the error above.
    echo  If detection found too few cards, try:
    echo    03_split_episode.bat "%VIDEO%" --sim 0.997
    echo    03_split_episode.bat "%VIDEO%" --min-duration 4
    echo  If it refused to cut ^(too many episodes^), preview frames were
    echo  saved automatically - check them, then rerun with --episodes N:
    echo    03_split_episode.bat "%VIDEO%" --episodes 3
    if exist "%REPO_ROOT%\output\preview\contact_sheet.jpg" explorer "%REPO_ROOT%\output\preview"
)
if "%RC%"=="0" (
    echo  Done! Episodes + split JSON are in output\
    explorer "%REPO_ROOT%\output"
)
echo ========================================
pause >nul
exit /b %RC%
