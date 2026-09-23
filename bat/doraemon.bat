@echo off
chcp 65001 >nul
cd /d "%~dp0.."
call .venv\Scripts\activate.bat >nul 2>&1
echo ========================================
echo   Doraemon Episode Fetch (TV Asahi)
echo ========================================
echo.
set ep=%~1
if "%ep%"=="" set /p ep="Episode number (e.g. 934): "
if "%ep%"=="" (
    echo No episode number given.
    pause
    goto end
)
.venv\Scripts\python.exe scripts\fetch_doraemon.py %ep%
echo.
echo ========================================
echo  Done! Press any key to close.
echo ========================================
pause >nul
:end
