@echo off
setlocal DisableDelayedExpansion
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
echo  Input : *_origin.srt + metadata.json with matching story IDs
echo  Output: updated metadata.json + *_jp_fixed.srt + *_zh_fixed.srt
echo  Folder: logs\.last_doraemon_materials (上次素材下载记录), 不需要输入
echo          --ask 才询问 / 或直接把目录当参数传进来
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
