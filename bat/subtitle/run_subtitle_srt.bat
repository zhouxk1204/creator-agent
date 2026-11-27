@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Anime Subtitle Extractor

rem ---------------------------------------------------------------------------
rem Extracts hard subtitles into an SRT file (stages 0-5: index, crop, detect,
rem group, OCR, SRT). Live progress on screen; the task's analysis.log is copied
rem to logs\ when the run ends.
rem
rem Usage:
rem   run_subtitle_srt.bat                                  ^(demo.mp4, Chinese OCR^)
rem   run_subtitle_srt.bat "D:\Anime\ep01.mp4"              ^(auto output\ep01_srt^)
rem   run_subtitle_srt.bat "D:\Anime\ep01.mp4" japan        ^(Japanese OCR^)
rem   run_subtitle_srt.bat "D:\Anime\ep01.mp4" japan  out\ep01
rem
rem Args: 1=input video, 2=OCR language ^(japan^|ch^|en^|korean, default ch^),
rem       3=output dir ^(must not exist, default output\<name>_srt^).
rem Runtime: about 3-4x the video length ^(decode + OCR^); a 10 min episode
rem takes roughly 35-45 min on a normal PC.
rem ---------------------------------------------------------------------------

call "%~dp0..\_bootstrap.bat"
if errorlevel 1 goto :fail

set "INPUT=%~1"
if "%INPUT%"=="" set "INPUT=demo.mp4"
set "LANG=%~2"
if "%LANG%"=="" set "LANG=ch"
set "OUTDIR=%~3"

if not exist "%INPUT%" (
    echo.
    echo [X] Input video not found: %INPUT%
    goto :fail
)
for %%I in ("%INPUT%") do set "STEM=%%~nI"
if "%OUTDIR%"=="" set "OUTDIR=output\%STEM%_srt"

set "EXE=%REPO_ROOT%\.venv\Scripts\anime-subtitle-extractor.exe"
if not exist "%EXE%" (
    echo.
    echo [X] CLI entry missing: %EXE%
    echo     Run: uv sync --extra subtitle-extractor
    goto :fail
)

echo.
echo [1/4] Checking OCR dependencies ^(paddlepaddle + paddleocr^)...
"%PY%" -c "import importlib.util as u,sys;sys.exit(0 if all(u.find_spec(m) for m in ('paddle','paddleocr','av') ) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [X] OCR dependencies are missing. Install them once:
    echo     uv pip install paddlepaddle paddleocr
    echo     ^(or: uv sync --extra subtitle-extractor --extra subtitle-ocr^)
    echo     Without them the tool still runs with --no-ocr, but produces no SRT.
    goto :fail
)

rem If this video was cropped before, reuse the crops: only detection, grouping,
rem OCR and SRT run again, so a retry takes minutes instead of a full decode.
set "REUSE="
if exist "%OUTDIR%\crops\crop_config.json" set "REUSE=%OUTDIR%"
if not defined REUSE goto :plan_fresh
set "OUTDIR=%REUSE%_ocr"
set "N=1"
:pick_reuse
if not exist "%OUTDIR%" goto :plan_reuse
set /a N+=1
set "OUTDIR=%REUSE%_ocr%N%"
goto :pick_reuse
:plan_reuse
set "MODE=Reuse crops (%REUSE%\crops): detect + group + OCR only"
goto :plan_done

:plan_fresh
if exist "%OUTDIR%" (
    echo.
    echo [X] Output already exists: %OUTDIR%
    echo     The tool never overwrites a task. Use a new folder, for example:
    echo         %~nx0 "%INPUT%" %LANG% output\%STEM%_srt2
    goto :fail
)
set "MODE=Full run: decode + crop + detect + group + OCR"
:plan_done

if not exist "%REPO_ROOT%\logs" mkdir "%REPO_ROOT%\logs"
set "N=0"
set "LOG=%REPO_ROOT%\logs\subtitle_%STEM%.log"
:picklog
if not exist "%LOG%" goto :log_ready
set /a N+=1
set "LOG=%REPO_ROOT%\logs\subtitle_%STEM%_%N%.log"
goto :picklog
:log_ready

echo.
echo [2/4] Task configuration
echo       input      : %INPUT%
echo       output     : %OUTDIR%
echo       OCR lang   : %LANG%
echo       log copy   : %LOG%
echo       mode       : %MODE%
echo.

echo [3/4] Running stages 0-5.
echo       Detection progress is live; one line per subtitle group appears while
echo       OCR runs. Do not close this window.
echo.

set "SUBTITLE_CONSOLE_LOG=1"
if defined REUSE (
    "%EXE%" --from-crops "%REUSE%" --output "%OUTDIR%" --ocr-language "%LANG%"
) else (
    "%EXE%" --input "%INPUT%" --output "%OUTDIR%" --no-save-frames --ocr-language "%LANG%" --crop-variants none
)
set "RC=%ERRORLEVEL%"

if exist "%OUTDIR%\analysis.log" (
    copy /y "%OUTDIR%\analysis.log" "%LOG%" >nul
) else (
    echo [i] analysis.log was not created - the task stopped before decoding.
)

if not "%RC%"=="0" (
    echo.
    echo [X] Extraction failed ^(exit code %RC%^).
    if exist "%OUTDIR%\task_status.json" (
        echo     task_status.json:
        type "%OUTDIR%\task_status.json"
    )
    echo     Full log: %OUTDIR%\analysis.log
    goto :fail_rc
)

echo.
echo [4/4] Reading the result summary...
"%PY%" "%REPO_ROOT%\scripts\subtitle_srt_report.py" "%OUTDIR%"
if errorlevel 1 goto :fail_rc

echo.
echo Done. SRT: %OUTDIR%\result.srt
echo Log copy: %LOG%
echo Audit: open groups\groups.json ^(field in_srt^) and
echo        debug\groups_visualization.jpg for the sampled group frames.
echo.
pause
exit /b 0

:fail_rc
pause
exit /b %RC%

:fail
echo.
pause
exit /b 1
