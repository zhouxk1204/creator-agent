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
echo   ComfyUI upscale run (live progress)
echo ========================================
echo.
echo  Usage: comfyui_upscale.bat               -^> submit workflow + show progress
echo         comfyui_upscale.bat --attach      -^> watch a job queued in the web UI
echo         comfyui_upscale.bat --video x.mp4 -^> override input video
echo         comfyui_upscale.bat --prefix name -^> override output prefix
echo.
"%PY%" scripts\comfyui_run.py %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
