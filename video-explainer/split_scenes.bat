@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
REM ============================================================
REM  video-explainer 第一阶段：视频分镜切割
REM
REM  用法：
REM    1. 双击本文件，按提示输入（或拖入）视频所在文件夹
REM    2. 或直接把 文件夹 / 视频文件 拖到本 bat 图标上
REM
REM  会自动取该文件夹下第一个视频进行分镜切割，
REM  输出到与视频同名的文件夹（原视频不会被修改）。
REM ============================================================

REM 如需覆盖已有的输出结果，把下一行改成: set "EXTRA_ARGS=--overwrite"
set "EXTRA_ARGS="

for %%I in ("%~dp0.") do set "PROJ=%%~fI"
cd /d "%PROJ%"

REM ---------- 准备 Python 环境（首次运行自动建 venv + 装依赖） ----------
set "PY=%PROJ%\.venv\Scripts\python.exe"
set "NEED_SETUP=0"
if not exist "%PY%" (
    set "NEED_SETUP=1"
) else (
    "%PY%" -c "import importlib.util as u,sys;sys.exit(0 if u.find_spec('scenedetect') else 1)" >nul 2>&1
    if errorlevel 1 set "NEED_SETUP=1"
)

if "%NEED_SETUP%"=="1" (
    echo 首次运行，正在准备 Python 环境，请稍候...
    where uv >nul 2>&1
    if errorlevel 1 if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    where uv >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [X] 未找到 uv，无法自动创建环境。请先安装 uv（无需管理员权限）：
        echo     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
        goto :fail
    )
    uv venv .venv
    if errorlevel 1 goto :fail
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
    if errorlevel 1 goto :fail
    echo.
)

REM ---------- 取得目标路径 ----------
set "TARGET=%~1"
if not defined TARGET set /p "TARGET=请输入视频所在文件夹路径（可把文件夹拖进本窗口）: "
set "TARGET=%TARGET:"=%"
if not defined TARGET (
    echo [X] 未输入路径。
    goto :fail
)
if not exist "%TARGET%" (
    echo [X] 路径不存在: %TARGET%
    goto :fail
)

REM ---------- 文件夹则找第一个视频；直接给的是视频文件则直接用 ----------
set "VIDEO="
for %%A in ("%TARGET%") do set "ATTR=%%~aA"
if "!ATTR:~0,1!"=="d" (
    for /f "delims=" %%F in ('dir /b /o:n "%TARGET%\*.mp4" "%TARGET%\*.mkv" "%TARGET%\*.avi" "%TARGET%\*.mov" "%TARGET%\*.ts" "%TARGET%\*.flv" "%TARGET%\*.wmv" 2^>nul') do (
        if not defined VIDEO set "VIDEO=%TARGET%\%%F"
    )
    if not defined VIDEO (
        echo [X] 该文件夹下没有找到视频文件: %TARGET%
        goto :fail
    )
) else (
    set "VIDEO=%TARGET%"
)

echo.
echo ========================================
echo   分镜切割 (Scene Split)
echo ========================================
echo   视频: %VIDEO%
echo   输出: 与视频同名的文件夹
echo ========================================
echo.

"%PY%" scripts\split_scenes.py "%VIDEO%" %EXTRA_ARGS%
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [OK] 全部完成。
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
