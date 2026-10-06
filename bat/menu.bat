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
echo   Creator Agent
echo ========================================
echo.
echo  1. doctor        (环境自检)
echo  2. sync          (同步昨天的视频)
echo  3. sync -d 3     (同步最近 3 天)
echo  4. creator list  (创作者列表)
echo  5. exit          (退出)
echo.
echo ========================================
set /p cmd="Select [1-5]: "
if "%cmd%"=="1" "%PY%" -m creator_agent.cli.main doctor
if "%cmd%"=="2" "%PY%" -m creator_agent.cli.main sync
if "%cmd%"=="3" "%PY%" -m creator_agent.cli.main sync --days 3
if "%cmd%"=="4" "%PY%" -m creator_agent.cli.main creator list
if "%cmd%"=="5" exit /b 0
if errorlevel 1 (
    echo.
    echo ========================================
    echo  FAILED - see the error above.
    echo ========================================
    pause >nul
    exit /b 1
)
echo.
pause >nul
exit /b 0
