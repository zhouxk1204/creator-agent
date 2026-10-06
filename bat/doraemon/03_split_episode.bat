@echo off
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
rem Episode splitter: detect title cards -> preview -> confirm -> cut.
rem
rem   03_split_episode.bat                          prompts for the video path
rem   03_split_episode.bat C:\test\935.mp4          path given directly (or drag & drop)
rem   03_split_episode.bat C:\test\935.mp4 --copy   extra args are passed through
rem
rem Flow: runs --preview first and opens output\preview\contact_sheet.jpg.
rem Check that every candidate really is a title card, then answer Y to cut
rem (writes output\935#1.mp4, 935#2.mp4, 935_split.json) or N to abort.
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
echo  Drag the video file into this window and press Enter,
echo  or paste its full path, e.g.  C:\test\935.mp4
echo.
set /p "VIDEO=Video path: "
rem strip surrounding quotes from a pasted path
set "VIDEO=%VIDEO:"=%"

:have_video
if "%VIDEO%"=="" (
    echo  No video given, aborting.
    pause >nul
    exit /b 1
)
if not exist "%VIDEO%" (
    echo  File not found: %VIDEO%
    pause >nul
    exit /b 1
)

echo.
echo ========================================
echo   [1/2] Preview detection: %VIDEO%
echo ========================================
echo.
"%PY%" scripts\split_episode.py "%VIDEO%" --preview%EXTRA%
if errorlevel 1 (
    echo.
    echo  Detection failed or found too few title cards.
    echo  Try: 03_split_episode.bat "%VIDEO%" --sim 0.997
    echo  or : 03_split_episode.bat "%VIDEO%" --min-duration 4
    pause >nul
    exit /b 1
)

set "SHEET=%REPO_ROOT%\output\preview\contact_sheet.jpg"
if exist "%SHEET%" start "" "%SHEET%"
echo.
echo  Contact sheet opened: output\preview\contact_sheet.jpg
echo  Check that every candidate IS a title card.
echo.
set /p "OK=Cut the video at the suggested split points? [Y/N]: "
if /i not "%OK%"=="Y" (
    echo  Aborted, nothing was cut.
    pause >nul
    exit /b 0
)

echo.
echo ========================================
echo   [2/2] Cutting: %VIDEO%
echo ========================================
echo.
"%PY%" scripts\split_episode.py "%VIDEO%"%EXTRA%
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" (
    echo  Done! Episodes + split JSON are in output\
    explorer "%REPO_ROOT%\output"
)
echo ========================================
pause >nul
exit /b %RC%
