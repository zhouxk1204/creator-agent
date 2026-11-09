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
rem   2. apply the tracked Douyin compatibility patches
rem   3. create a dedicated Python 3.12 venv (sau requires <3.13)
rem   4. install sau + playwright into that venv
rem   5. install the patchright chromium browser
rem   6. create conf.py from the example
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

rem Apply our Douyin fixes without tracking the third-party repo in this one.
rem Keep these as separate patches so existing installs with the cover patch
rem can add the upload reliability fix without reverting anything first.
for %%P in (douyin-dual-cover douyin-upload-reliability) do (
    call :apply_sau_patch "%%~P"
    if errorlevel 1 goto :fail
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

rem Patchright 1.58.2 rewrites Douyin's CSP and blocks the upload blob Worker,
rem leaving "parsing file" at 0%% until the page reports a network failure.
rem Pin the upstream-verified fixed version even if the cloned sau still pins 1.58.2.
"%SAU_PY%" -c "import importlib.metadata as m,sys;sys.exit(0 if m.version('patchright')=='1.64.4' else 1)" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing Patchright 1.64.4 for Douyin upload compatibility ...
    uv pip install --python "%SAU_PY%" "patchright==1.64.4"
    if errorlevel 1 goto :fail
) else (
    echo [OK] Patchright 1.64.4 already installed
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

:apply_sau_patch
set "SAU_PATCH=%REPO_ROOT%\patches\social-auto-upload\%~1.patch"
if not exist "%SAU_PATCH%" (
    echo [X] Missing compatibility patch: %SAU_PATCH%
    exit /b 1
)
git -C "%SAU_DIR%" apply --reverse --check "%SAU_PATCH%" >nul 2>&1
if not errorlevel 1 (
    echo [OK] %~1 patch already applied
    exit /b 0
)
git -C "%SAU_DIR%" apply --check "%SAU_PATCH%" >nul 2>&1
if errorlevel 1 (
    echo [X] %~1 patch does not match this sau revision.
    echo     Update the patch before publishing.
    exit /b 1
)
git -C "%SAU_DIR%" apply "%SAU_PATCH%"
if errorlevel 1 exit /b 1
echo [OK] %~1 patch applied
exit /b 0

:fail
echo.
echo [X] setup failed - see the message above.
pause >nul
exit /b 1
