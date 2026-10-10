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
echo   Character Library Tests
echo   (synthetic data - no real footage needed)
echo ========================================
echo.
echo  Runs all tests/unit/test_character_*.py:
echo    - sampling / filter / naming rules
echo    - manifest merge + label syncing
echo    - end-to-end extract -^> contact sheet -^> sync
echo.
"%PY%" -m pytest tests\unit -k character -v %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the errors above.
if "%RC%"=="0" echo  All character library tests passed!
echo ========================================
pause >nul
exit /b %RC%
