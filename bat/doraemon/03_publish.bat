@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Doraemon Publisher
call "%~dp0..\_bootstrap.bat"
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only. Chinese usage docs: bat\doraemon_legacy\03_publish.md.
rem Default: Bilibili (_b videos + optimized 4K covers), then Douyin
rem (_dy exports + portrait/landscape covers), with a separate upload confirmation.
rem Uses logs\.last_doraemon_materials unless a materials path is supplied.
rem --only bilibili / --only douyin selects one platform.
rem Each platform is independent; successes are recorded to skip on retries.
rem Missing zh.synopsis/tags are translated first, before any platform step.
rem With no _b video at all Bilibili is skipped and Douyin still runs.
rem --story, --dry-run, --yes, --ask and publisher options are forwarded.
rem SHIFT changes numbered arguments, but leaves %* intact for all steps.
rem ---------------------------------------------------------------------------

set "PLATFORM=both"
:parse_args
if "%~1"=="" goto :check_platform
if /i "%~1"=="--only" (
    set "PLATFORM=%~2"
    shift
)
if /i "%~1"=="--only=bilibili" set "PLATFORM=bilibili"
if /i "%~1"=="--only=douyin" set "PLATFORM=douyin"
shift
goto :parse_args

:check_platform
if /i "%PLATFORM%"=="both" goto :setup
if /i "%PLATFORM%"=="bilibili" goto :setup
if /i "%PLATFORM%"=="douyin" goto :setup
echo [X] --only must be bilibili or douyin.
goto :fail

:setup
set "RC=0"
set "SAU_PY=%REPO_ROOT%\tools\social-auto-upload\.venv\Scripts\python.exe"
if not exist "%SAU_PY%" (
    echo.
    echo [X] social-auto-upload is not installed yet.
    echo     Run this setup first: bat\setup_publish.bat
    echo.
    goto :fail
)

rem ---------------------------------------------------------------------------
rem Step 0: every upload needs zh.synopsis and platform tags. Translate first
rem when they are missing, then continue with the platform steps below.
rem ---------------------------------------------------------------------------
echo.
echo [Prepare] Check the Chinese synopsis and tags in metadata.json.
echo.
"%PY%" "%REPO_ROOT%\scripts\ensure_doraemon_translation.py" %* --check
if errorlevel 4 goto :translate_first
if errorlevel 1 goto :fail
goto :prepare_done

:translate_first
echo.
echo [Prepare] Chinese synopsis/tags are missing - translating before publishing.
echo.
call :ensure_server
if errorlevel 1 goto :fail
"%PY%" "%REPO_ROOT%\scripts\ensure_doraemon_translation.py" %*
if errorlevel 1 goto :translate_failed
if "%STARTED_SERVER%"=="1" taskkill /IM llama-server.exe /F >nul 2>&1
set "STARTED_SERVER=0"
goto :prepare_done

:translate_failed
if "%STARTED_SERVER%"=="1" taskkill /IM llama-server.exe /F >nul 2>&1
set "STARTED_SERVER=0"
echo.
echo [X] Translation failed - fix it, then publish again.
goto :fail

:prepare_done
if /i "%PLATFORM%"=="douyin" goto :douyin

echo.
echo [Bilibili 1/2] Optimize 4K covers for the Bilibili cover endpoint.
echo.
"%PY%" "%REPO_ROOT%\scripts\make_bilibili_covers.py" %*
rem Exit code 3 means there is no _b video at all: skip Bilibili, do not abort.
if errorlevel 3 goto :no_bilibili
if errorlevel 1 goto :bilibili_failed

echo.
echo [Bilibili 2/2] Publish every _b video. Confirm actual uploads with Y.
echo                Live biliup progress, speed and ETA will be shown below.
echo.
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %* --b --only bilibili
if errorlevel 1 goto :bilibili_failed
goto :after_bilibili

:bilibili_failed
set "RC=1"
echo [X] Bilibili failed or was cancelled. Douyin will still be attempted if selected.
goto :after_bilibili

:no_bilibili
echo.
echo [Bilibili] No _b video in the materials folder - skipping Bilibili.

:after_bilibili
if /i "%PLATFORM%"=="bilibili" goto :done

:douyin
echo.
echo [Douyin 1/2] Make portrait and landscape covers for _dy exports.
echo.
"%PY%" "%REPO_ROOT%\scripts\make_douyin_covers.py" %* --dy-only
if errorlevel 1 goto :douyin_failed

echo.
echo [Douyin 2/2] Publish every _dy video. Headed mode; confirm uploads with Y.
echo.
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %* --dy --only douyin --headed
if errorlevel 1 goto :douyin_failed
goto :done

:douyin_failed
set "RC=1"
echo [X] Douyin failed or was cancelled. Completed Bilibili uploads are preserved.

:done
echo.
if not "%RC%"=="0" echo FAILED or cancelled ^(exit code %RC%^) - see above.
if "%RC%"=="0" echo Done.
echo.
pause
exit /b %RC%

rem ---------------------------------------------------------------------------
:ensure_server
rem Start the local translation LLM only when something really needs translating.
rem NOTE: must be -sf (fail on HTTP error) against /health - llama-server returns
rem 503 on /health while the model is still loading, and /v1/models answers 200
rem the moment the HTTP listener is up, long before inference works.
curl -sf -m 3 http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 (
    echo [server] translation LLM already running.
    exit /b 0
)
echo [server] starting translation LLM ^(llama.cpp^) in a separate window...
start "translate-server" /min "%~dp0..\start_translate_server.bat"
set "STARTED_SERVER=1"
set /a TRIES=0
:wait_server
curl -sf -m 3 http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 (
    echo [server] ready.
    exit /b 0
)
set /a TRIES+=1
if %TRIES% GEQ 60 (
    echo [server] did not become ready in time.
    taskkill /IM llama-server.exe /F >nul 2>&1
    exit /b 1
)
rem Use the absolute path: timeout lives in System32 and a stripped-down PATH
rem (launched from some editors or scripts) cannot find it by name.
"%SystemRoot%\System32\timeout.exe" /t 2 /nobreak >nul
goto :wait_server

:fail
echo.
pause
exit /b 1
