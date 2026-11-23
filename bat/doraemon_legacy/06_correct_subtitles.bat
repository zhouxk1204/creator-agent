@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
rem NOTE: keep this file ASCII-only. Non-ASCII text after "chcp 65001" makes
rem cmd.exe mis-parse the line and run half of it as a command.
rem Chinese usage docs: 06_correct_subtitles.md.
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
echo  Input : *_origin.srt + metadata.json with matching story IDs
echo  Output: updated metadata.json + *_jp_fixed.srt + *_zh_fixed.srt
echo  Folder: logs\.last_doraemon_materials (no path prompt needed)
echo          --ask to choose another folder, or pass a path as an argument
echo  One LLM server is shared by all stages.
echo.

"%PY%" scripts\correct_doraemon_subtitles.py %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%
