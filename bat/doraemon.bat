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
echo   Doraemon Episode Fetch (TV Asahi)
echo ========================================
echo.
set ep=%~1
if "%ep%"=="" set /p ep="Episode number (e.g. 934): "
if "%ep%"=="" (
    echo No episode number given.
    pause >nul
    exit /b 1
)
"%PY%" scripts\fetch_doraemon.py %ep%
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
