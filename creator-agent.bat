@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat >nul 2>&1
echo ========================================
echo   Creator Agent
echo ========================================
echo.
echo  1. doctor     (环境检查)
echo  2. sync       (同步今日视频)
echo  3. sync -d 3  (回溯3天)
echo  4. creator list (创作者列表)
echo  5. exit
echo.
echo ========================================
set /p cmd="Select [1-5]: "
if "%cmd%"=="1" .venv\Scripts\python.exe -m creator_agent.cli.main doctor
if "%cmd%"=="2" .venv\Scripts\python.exe -m creator_agent.cli.main sync
if "%cmd%"=="3" .venv\Scripts\python.exe -m creator_agent.cli.main sync --days 3
if "%cmd%"=="4" .venv\Scripts\python.exe -m creator_agent.cli.main creator list
if "%cmd%"=="5" goto end
echo.
pause
:end
