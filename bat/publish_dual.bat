@echo off
chcp 65001 >nul
call "%~dp0_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only (see 04_ja_asr.bat for why).
rem Chinese usage docs live in publish_dual.md next to this file.
rem
rem One-click publish a prepared folder to Douyin + Bilibili via
rem social-auto-upload (sau). Prepare publish\<name>\ with video + title.txt
rem (+ optional cover.jpg / desc.txt / tags.txt / meta.txt), then:
rem
rem   publish_dual.bat                      auto-pick the only candidate folder
rem   publish_dual.bat publish\my-video     explicit folder
rem   publish_dual.bat --dry-run            print sau commands, publish nothing
rem   publish_dual.bat --only bilibili      retry just one platform
rem ---------------------------------------------------------------------------

set "SAU_PY=%REPO_ROOT%\tools\social-auto-upload\.venv\Scripts\python.exe"
if not exist "%SAU_PY%" (
    echo.
    echo [X] social-auto-upload is not installed yet.
    echo     Run the one-time setup first:  bat\setup_publish.bat
    echo.
    pause >nul
    exit /b 1
)

echo ========================================
echo   Publish to Douyin + Bilibili
echo ========================================
echo.
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo  FAILED or cancelled ^(exit code %RC%^) - see above.
if "%RC%"=="0" echo  All done!
pause >nul
exit /b %RC%
