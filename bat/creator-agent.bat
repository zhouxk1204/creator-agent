@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat >nul 2>&1
echo ========================================
echo   Creator Agent
echo ========================================
echo.
echo  1. doctor     (�������)
echo  2. sync       (ͬ��������Ƶ)
echo  3. sync -d 3  (����3��)
echo  4. creator list (�������б�)
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
