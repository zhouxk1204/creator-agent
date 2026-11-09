@echo off
chcp 65001 >nul
setlocal
title Douyin Publisher
call "%~dp0_bootstrap.bat"
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only. Chinese usage docs: publish_douyin.md.
rem
rem Double-click, paste a publish folder path, and press Enter.
rem A folder can also be dragged onto this BAT or passed as the first argument.
rem The normal publish summary and Y confirmation are always shown.
rem ---------------------------------------------------------------------------

set "SAU_PY=%REPO_ROOT%\tools\social-auto-upload\.venv\Scripts\python.exe"
if not exist "%SAU_PY%" (
    echo.
    echo [X] social-auto-upload is not installed yet.
    echo     Run this setup first: bat\setup_publish.bat
    echo.
    goto :fail
)

set "PUBLISH_DIR=%~1"
if not defined PUBLISH_DIR (
    echo.
    echo Paste the folder containing video.mp4 and title.txt.
    set /p "PUBLISH_DIR=Publish folder path: "
)

rem Pasted paths often include surrounding quotes.
set "PUBLISH_DIR=%PUBLISH_DIR:"=%"
if not defined PUBLISH_DIR (
    echo.
    echo [X] No publish folder was entered.
    goto :fail
)
if not exist "%PUBLISH_DIR%\NUL" (
    echo.
    echo [X] Folder does not exist: %PUBLISH_DIR%
    goto :fail
)

echo.
echo Starting Douyin publish in headed mode. Confirm the summary with Y.
echo.
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" "%PUBLISH_DIR%" --only douyin --headed
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo FAILED or cancelled ^(exit code %RC%^) - see above.
if "%RC%"=="0" echo Douyin publish completed.
echo.
pause
exit /b %RC%

:fail
echo.
pause
exit /b 1
