@echo off
chcp 65001 >nul
call "%~dp0_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)
call .venv\Scripts\activate.bat >nul 2>&1
echo ========================================
echo   Doraemon Episode Fetch (TV Asahi)
echo   + translate title/synopsis to Chinese
echo ========================================
echo.
set ep=%~1
if "%ep%"=="" set /p ep="Episode number (e.g. 934): "
if "%ep%"=="" (
    echo No episode number given.
    pause >nul
    exit /b 1
)
"%PY%" scripts\fetch_doraemon.py %ep%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :done

rem ---------------------------------------------------------------------------
rem Step 2: translate the per-story notes ({ep}_{i}.md -> {ep}_{i}_zh.md).
rem Needs the local LLM server (llama.cpp) - same one ja-asr.bat uses. Start
rem it if it's down; if WE started it, stop it at the end to free VRAM/RAM.
rem ---------------------------------------------------------------------------
set "STARTED_SERVER=0"
call :ensure_server
if errorlevel 1 (
    set "RC=1"
    goto :done
)
"%PY%" scripts\translate_doraemon.py %ep%
set "RC=%ERRORLEVEL%"

:done
if "%STARTED_SERVER%"=="1" (
    echo.
    echo [server] stopping translation LLM to free VRAM/RAM...
    taskkill /IM llama-server.exe /F >nul 2>&1
)
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Press any key to close.
echo ========================================
pause >nul
exit /b %RC%

rem ---------------------------------------------------------------------------
:ensure_server
rem NOTE: must be -sf (fail on HTTP error) against /health - llama-server
rem returns 503 on /health while the model is still loading, and /v1/models
rem answers 200 the moment the HTTP listener is up, long before inference works.
curl -sf -m 3 http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 (
    echo [server] translation LLM already running.
    exit /b 0
)
echo [server] starting translation LLM ^(llama.cpp^) in a separate window...
start "translate-server" /min "%~dp0translate-server.bat"
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
timeout /t 2 /nobreak >nul
goto :wait_server
