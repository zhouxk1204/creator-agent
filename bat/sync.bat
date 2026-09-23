@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat >nul 2>&1
echo ========================================
echo   Creator Agent - Sync
echo ========================================
echo.
.venv\Scripts\python.exe -m creator_agent.cli.main sync %*
echo.
echo ========================================
echo  Done! Press any key to close.
echo ========================================
pause >nul
