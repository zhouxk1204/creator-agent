@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Douyin Publisher
call "%~dp0_bootstrap.bat"
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only. Chinese usage docs: publish_douyin.md.
rem Double-click to choose a materials folder or metadata.json in Python.
rem Enter accepts the saved materials folder. Multiple stories need a selection.
rem All arguments are forwarded, including --story and --dry-run.
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
echo Douyin metadata publisher. Headed mode; confirm actual uploads with Y.
echo.
if not "%~1"=="" goto :with_args
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" --prompt-target --only douyin --headed
goto :done

:with_args
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %* --only douyin --headed

:done
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
