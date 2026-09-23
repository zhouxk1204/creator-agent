@echo off
chcp 65001 >nul
cd /d "%~dp0.."
call .venv\Scripts\activate.bat >nul 2>&1
echo ========================================
echo   TVer Download (default: Doraemon latest)
echo ========================================
echo.
echo  Usage: tver.bat             -^> download latest Doraemon episode
echo         tver.bat --list      -^> list available episodes only
echo         tver.bat --all       -^> download all available episodes
echo         tver.bat --episode epenb4xglc
echo         tver.bat --series ^<id_or_url^>
echo.
.venv\Scripts\python.exe scripts\download_tver.py %*
echo.
echo ========================================
echo  Done! Press any key to close.
echo ========================================
pause >nul
