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
rem Chinese usage docs live in publish_dual.md next to publish_dual.bat.
rem
rem One-time setup for social-auto-upload (sau):
rem   1. clone the repo into tools\social-auto-upload (if missing)
rem   2. create a dedicated Python 3.12 venv (sau requires <3.13)
rem   3. install sau + playwright into that venv
rem   4. install the patchright chromium browser
rem   5. create conf.py from the example
rem Safe to re-run: every step is skipped when already done.
rem ---------------------------------------------------------------------------

set "SAU_DIR=%REPO_ROOT%\tools\social-auto-upload"

rem -- uv is needed below; _bootstrap already made sure it is on PATH. --

if not exist "%SAU_DIR%\.git" (
    echo [*] Cloning social-auto-upload into tools\social-auto-upload ...
    git clone --depth 50 https://github.com/dreammis/social-auto-upload.git "%SAU_DIR%"
    if errorlevel 1 goto :fail
) else (
    echo [OK] repo already cloned: %SAU_DIR%
)

set "SAU_PY=%SAU_DIR%\.venv\Scripts\python.exe"
if not exist "%SAU_PY%" (
    echo [*] Creating Python 3.12 venv for sau ...
    uv venv --python 3.12 "%SAU_DIR%\.venv"
    if errorlevel 1 goto :fail
) else (
    echo [OK] venv exists
)

"%SAU_PY%" -c "import importlib.util as u,sys;sys.exit(0 if u.find_spec('sau_cli') else 1)" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing sau into its venv ...
    uv pip install --python "%SAU_PY%" -e "%SAU_DIR%"
    if errorlevel 1 goto :fail
) else (
    echo [OK] sau package installed
)

rem sau_cli eagerly imports playwright.async_api (baijiahao uploader) even
rem though the runtime browser is patchright - install plain playwright too.
"%SAU_PY%" -c "import importlib.util as u,sys;sys.exit(0 if u.find_spec('playwright') else 1)" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing playwright (import dependency of sau) ...
    uv pip install --python "%SAU_PY%" playwright
    if errorlevel 1 goto :fail
) else (
    echo [OK] playwright installed
)

if not exist "%SAU_DIR%\conf.py" (
    echo [*] Creating conf.py from conf.example.py ...
    copy /y "%SAU_DIR%\conf.example.py" "%SAU_DIR%\conf.py" >nul
    if errorlevel 1 goto :fail
) else (
    echo [OK] conf.py exists
)

echo [*] Installing patchright chromium browser (first run downloads ~100MB) ...
"%SAU_DIR%\.venv\Scripts\patchright.exe" install chromium
if errorlevel 1 goto :fail

echo.
echo ========================================
echo   sau setup complete!
echo ========================================
echo.
echo Next ONE-TIME step: log in both platforms (interactive, needs you
echo to scan QR codes). Run these two commands in a terminal:
echo.
echo   %SAU_DIR%\.venv\Scripts\sau.exe douyin   login --account default --headed
echo   %SAU_DIR%\.venv\Scripts\sau.exe bilibili login --account default
echo.
echo Then publish with:  bat\publish_dual.bat
echo.
pause >nul
exit /b 0

:fail
echo.
echo [X] setup failed - see the message above.
pause >nul
exit /b 1
