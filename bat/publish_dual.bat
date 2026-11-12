@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
call "%~dp0_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only. Chinese usage docs: publish_dual.md.
rem Publish one story from metadata.json via social-auto-upload (sau).
rem Double-click: choose a materials folder (Enter uses the saved default).
rem   publish_dual.bat "C:\path\doraemon_941" --story 941_1 --dry-run
rem   publish_dual.bat "C:\path\metadata.json" --story 941_1 --only bilibili
rem Python handles quoted interactive paths and story selection.
rem ---------------------------------------------------------------------------

set "SAU_PY=%REPO_ROOT%\tools\social-auto-upload\.venv\Scripts\python.exe"
if not exist "%SAU_PY%" (
    echo.
    echo [X] social-auto-upload is not installed yet.
    echo     Run the one-time setup first: bat\setup_publish.bat
    echo.
    pause >nul
    exit /b 1
)

echo ========================================
echo   Publish metadata story to Douyin + Bilibili
echo ========================================
echo.
if not "%~1"=="" goto :with_args
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" --prompt-target
goto :done

:with_args
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %*

:done
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo  FAILED or cancelled ^(exit code %RC%^) - see above.
if "%RC%"=="0" echo  All done!
pause >nul
exit /b %RC%
