@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Doraemon Publisher
call "%~dp0..\_bootstrap.bat"
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------------------
rem NOTE: keep this file ASCII-only. Chinese usage docs: 03_publish.md.
rem Default: Bilibili (_b videos + optimized 4K covers), then Douyin
rem (_dy exports + portrait/landscape covers), with a separate upload confirmation.
rem Uses logs\.last_doraemon_materials unless a materials path is supplied.
rem --only bilibili / --only douyin selects one platform.
rem --story, --dry-run, --yes, --ask and publisher options are forwarded.
rem SHIFT changes numbered arguments, but leaves %* intact for all four steps.
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
set "SAU_PY=%REPO_ROOT%\tools\social-auto-upload\.venv\Scripts\python.exe"
if not exist "%SAU_PY%" (
    echo.
    echo [X] social-auto-upload is not installed yet.
    echo     Run this setup first: bat\setup_publish.bat
    echo.
    goto :fail
)
if /i "%PLATFORM%"=="douyin" goto :douyin

echo.
echo [Bilibili 1/2] Optimize 4K covers for the Bilibili cover endpoint.
echo.
"%PY%" "%REPO_ROOT%\scripts\make_bilibili_covers.py" %*
if errorlevel 1 goto :fail

echo.
echo [Bilibili 2/2] Publish every _b video. Confirm actual uploads with Y.
echo                Live biliup progress, speed and ETA will be shown below.
echo.
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %* --b --only bilibili
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :done
if /i "%PLATFORM%"=="bilibili" goto :done

:douyin
echo.
echo [Douyin 1/2] Make portrait and landscape covers for _dy exports.
echo.
"%PY%" "%REPO_ROOT%\scripts\make_douyin_covers.py" %* --dy-only
if errorlevel 1 goto :fail

echo.
echo [Douyin 2/2] Publish every _dy video. Headed mode; confirm uploads with Y.
echo.
"%SAU_PY%" "%REPO_ROOT%\scripts\publish_dual.py" %* --dy --only douyin --headed
set "RC=%ERRORLEVEL%"

:done
echo.
if not "%RC%"=="0" echo FAILED or cancelled ^(exit code %RC%^) - see above.
if "%RC%"=="0" echo Done.
echo.
pause
exit /b %RC%

:fail
echo.
pause
exit /b 1
