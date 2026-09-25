@echo off
chcp 65001 >nul
REM ============================================================
REM  Shared setup for every launcher in bat\.
REM
REM  Called as:   call "%~dp0_bootstrap.bat"
REM               if errorlevel 1 goto :fail
REM
REM  On success it sets REPO_ROOT and PY and chdir's to the repo
REM  root. If .venv is missing, or its dependencies were never
REM  installed, it runs `uv sync` on its own -- so double-clicking
REM  a launcher works on a fresh clone with no manual steps.
REM ============================================================

for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"
set "PY=%REPO_ROOT%\.venv\Scripts\python.exe"

REM Check the venv exists AND has the packages from pyproject.toml.
REM find_spec() only probes for the module, it does not import it.
set "NEED_SYNC=0"
if not exist "%PY%" (
    set "NEED_SYNC=1"
) else (
    "%PY%" -c "import importlib.util as u,sys;sys.exit(0 if all(u.find_spec(m) for m in ('httpx','typer','yt_dlp','creator_agent')) else 1)" >nul 2>&1
    if errorlevel 1 set "NEED_SYNC=1"
)

if "%NEED_SYNC%"=="0" exit /b 0

REM -- Need uv to build the venv. Fall back to the default install dir. --
where uv >nul 2>&1
if errorlevel 1 if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo [X] uv is not installed, so the Python environment cannot be set up.
    echo     Install it once ^(no admin rights needed^), then re-run this file:
    echo.
    echo         powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo.
    exit /b 1
)

echo.
echo [*] Python environment not ready - running "uv sync" ^(first run only^).
echo     This may take a few minutes and needs an internet connection.
echo.
uv sync
if errorlevel 1 (
    echo.
    echo [X] "uv sync" failed. Check your network connection and try again.
    echo.
    exit /b 1
)

if not exist "%PY%" (
    echo.
    echo [X] "uv sync" finished but "%PY%" is still missing.
    echo.
    exit /b 1
)

exit /b 0
