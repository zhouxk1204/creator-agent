@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Bilibili Publisher
call "%~dp0_bootstrap.bat"
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only. Chinese usage docs: publish_bilibili.md.
rem
rem Double-click flow (materials folder from logs\.last_doraemon_materials):
rem   Publish every <story>_b.mp4 in the materials folder to Bilibili.
rem   Title: metadata publish.title ("Doraemon <episode> <chinese title>")
rem   Desc : metadata zh.synopsis, including all line breaks
rem   Cover: <story>_4k.jpg
rem
rem All arguments are forwarded: --story 942_1, --dry-run, --yes, --ask.
rem ---------------------------------------------------------------------------

set "SAU_PY=%REPO_ROOT%\tools\social-auto-upload\.venv\Scripts\python.exe"
if not exist "%SAU_PY%" (
    echo.
    echo [X] social-auto-upload is not installed yet.
    echo     Run this setup first: bat\setup_publish.bat
    echo.
    goto :fail
)

echo.
echo Publish every _b video to Bilibili. Confirm actual uploads with Y.
echo.
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %* --b --only bilibili
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo FAILED or cancelled ^(exit code %RC%^) - see above.
if "%RC%"=="0" echo Done.
echo.
pause
exit /b %RC%

:fail
echo.
pause
exit /b 1
