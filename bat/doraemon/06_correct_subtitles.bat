@echo off
chcp 65001 >nul
call "%~dp0..\_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)
call .venv\Scripts\activate.bat >nul 2>&1

echo ========================================
echo   Synopsis + tags -^> subtitle correction + JA-ZH
echo ========================================
echo.
echo  Input : *_origin.srt + matching *_jp.md
echo  Output: *_zh.md + *_jp_fixed.srt + *_zh_fixed.srt
echo  One LLM server is shared by all stages.
echo.

if not "%~1"=="" goto :with_args
set "TARGET="
set /p TARGET="Materials folder (Enter = latest Desktop folder): "
if "%TARGET%"=="" (
    "%PY%" scripts\correct_doraemon_subtitles.py
) else (
    "%PY%" scripts\correct_doraemon_subtitles.py "%TARGET%"
)
goto :done

:with_args
"%PY%" scripts\correct_doraemon_subtitles.py %*

:done
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
