@echo off
setlocal
chcp 65001 >nul

rem Project-local wrapper for the Real-ESRGAN workflow. The engine, model, and
rem dedicated upscale conda environment remain in their existing locations.
set "UPSCALE_PYTHON=C:\Users\34696\miniconda3\envs\upscale\python.exe"
set "UPSCALE_SCRIPT=%~dp0..\..\scripts\upscale_tool.py"

if not exist "%UPSCALE_PYTHON%" (
    echo [upscale] Python not found: %UPSCALE_PYTHON%
    exit /b 1
)
if not exist "%UPSCALE_SCRIPT%" (
    echo [upscale] Script not found: %UPSCALE_SCRIPT%
    exit /b 1
)

"%UPSCALE_PYTHON%" "%UPSCALE_SCRIPT%"
exit /b %ERRORLEVEL%
