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
echo ========================================
echo   TVer Download (default: Doraemon latest)
echo ========================================
echo.
echo  Usage: 01_download_tver.bat             -^> download latest Doraemon episode
echo         01_download_tver.bat --list      -^> list available episodes only
echo         01_download_tver.bat --all       -^> download all available episodes
echo         01_download_tver.bat --episode epenb4xglc
echo         01_download_tver.bat --series ^<id_or_url^>
echo.
"%PY%" scripts\download_tver.py %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
