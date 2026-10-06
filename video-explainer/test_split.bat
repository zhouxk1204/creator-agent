@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
REM ============================================================
REM  分镜切割测试（直接执行版）
REM
REM  用法：
REM    1. 双击运行 —— 默认切 %DEFAULT_VIDEO%
REM    2. 把视频文件拖到这个 bat 上 —— 切拖入的视频
REM    3. 命令行: test_split.bat "D:\path\video.mp4"
REM
REM  与 split_scenes.bat 的区别：
REM    - 免输入，带默认测试视频
REM    - 自动 --overwrite，可反复重跑
REM ============================================================

set "DEFAULT_VIDEO=C:\test\temps\test.mp4"

for %%I in ("%~dp0.") do set "PROJ=%%~fI"
cd /d "%PROJ%"

REM FFmpeg 不在 PATH 时，指定本机的 ffmpeg（按需修改）
if not defined FFMPEG_PATH if exist "%USERPROFILE%\Downloads\ffmpeg-2026-03-01-git-862338fe31-full_build\bin\ffmpeg.exe" set "FFMPEG_PATH=%USERPROFILE%\Downloads\ffmpeg-2026-03-01-git-862338fe31-full_build\bin\ffmpeg.exe"
if not defined FFPROBE_PATH if exist "%USERPROFILE%\Downloads\ffmpeg-2026-03-01-git-862338fe31-full_build\bin\ffprobe.exe" set "FFPROBE_PATH=%USERPROFILE%\Downloads\ffmpeg-2026-03-01-git-862338fe31-full_build\bin\ffprobe.exe"

set "PY=%PROJ%\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [X] 未找到 .venv，请先运行一次 split_scenes.bat 完成环境初始化。
    goto :fail
)
"%PY%" -c "import scenedetect" >nul 2>&1
if errorlevel 1 (
    echo [X] venv 里缺 scenedetect，请先运行一次 split_scenes.bat 装依赖。
    goto :fail
)

set "VIDEO=%~1"
if not defined VIDEO set "VIDEO=%DEFAULT_VIDEO%"
set "VIDEO=%VIDEO:"=%"
if not exist "%VIDEO%" (
    echo [X] 视频不存在: %VIDEO%
    echo     把视频拖到本 bat 上，或修改 bat 里的 DEFAULT_VIDEO。
    goto :fail
)

echo.
echo ========================================
echo   分镜切割测试
echo ========================================
echo   视频: %VIDEO%
echo   输出: 与视频同名的文件夹（自动覆盖）
echo ========================================
echo.

"%PY%" scripts\split_scenes.py "%VIDEO%" --overwrite
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [OK] 分镜切割完成，到输出文件夹看 scenes.json 和分段视频。
) else (
    echo [X] 未完全成功，退出码 %RC%。详见输出文件夹下的 scene_split.log
)
echo.
pause
exit /b %RC%

:fail
echo.
pause
exit /b 1
