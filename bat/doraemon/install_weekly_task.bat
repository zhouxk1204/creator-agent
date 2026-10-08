@echo off
chcp 65001 >nul
REM ============================================================
REM  Register weekly_sync.bat in Windows Task Scheduler:
REM    every Saturday 19:00 local time (keep this PC on China
REM    time - TVer updates 17:00 JST = 16:00 CST, so 19:00 CST
REM    leaves a 3-hour buffer).
REM
REM  Run this once (double-click is enough). Re-run any time to
REM  update the schedule (/F overwrites). The task runs as the
REM  current user and only while logged on - leave the PC on and
REM  logged in Saturday evening.
REM ============================================================

set "TASK=DoraemonWeeklySync"
set "SCRIPT=%~dp0weekly_sync.bat"

schtasks /Create /TN "%TASK%" /TR "\"%SCRIPT%\"" /SC WEEKLY /D SAT /ST 19:00 /F
if errorlevel 1 (
    echo.
    echo [X] Failed to create the task - try right-click -^> Run as administrator.
    pause
    exit /b 1
)

echo.
echo [OK] Task "%TASK%" created: every Saturday 19:00 runs
echo      %SCRIPT%
echo.
echo  Test run now :  schtasks /Run /TN %TASK%
echo  Check status :  schtasks /Query /TN %TASK% /V /FO LIST
echo  Remove       :  schtasks /Delete /TN %TASK% /F
echo  Log file     :  logs\weekly_doraemon.log
echo.
pause
