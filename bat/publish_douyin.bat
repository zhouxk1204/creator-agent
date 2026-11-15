@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Douyin Publisher
call "%~dp0_bootstrap.bat"
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only. Chinese usage docs: publish_douyin.md.
rem
rem Double-click flow (materials folder from logs\.last_doraemon_materials):
rem   1. make the two Douyin covers of every story that needs them
rem      (portrait 3:4 + landscape 4:3, recorded in metadata.json)
rem   2. publish every <story>_dy\<story>_dy.mp4 from the JianYing export, one
rem      Douyin video per file, with the metadata title / tags / covers
rem
rem Title: metadata publish.title ("<episode prefix> <chinese title>")
rem Desc : the Douyin tags themselves (#tag #tag ...)
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
echo [1/2] Douyin covers (portrait + landscape)
echo.
"%PY%" "%REPO_ROOT%\scripts\make_douyin_covers.py" %* --dy-only
if errorlevel 1 goto :fail

echo.
echo [2/2] Publish _dy videos. Headed mode; confirm actual uploads with Y.
echo.
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %* --dy --only douyin --headed
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
