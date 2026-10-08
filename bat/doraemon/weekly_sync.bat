@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  Weekly unattended Doraemon pipeline (registered in Task
REM  Scheduler by install_weekly_task.bat - every Saturday 19:00,
REM  3h after the 17:00 JST TVer update):
REM
REM    1. download_tver.py           latest episode from TVer
REM    2. fetch_doraemon.py --auto   episode info for every newly
REM                                  aired episode (catches up
REM                                  missed weeks), numbers land in
REM                                  storage\doraemon\.last_auto_fetched
REM    3. translate_doraemon.py      per fetched episode (starts
REM                                  the llama.cpp server if down,
REM                                  stops it again if WE started it)
REM
REM  Unlike 01/02 this never pauses; everything is appended to
REM  logs\weekly_doraemon.log. Later, once ASR quality is there,
REM  chain 03_split_episode / 04_ja_asr / 05_learn_corrections here.
REM ============================================================

call "%~dp0..\_bootstrap.bat"
if errorlevel 1 exit /b 1

if not exist "%REPO_ROOT%\logs" mkdir "%REPO_ROOT%\logs"
set "LOG=%REPO_ROOT%\logs\weekly_doraemon.log"
set "STATE=%REPO_ROOT%\storage\doraemon\.last_auto_fetched"
set "RC=0"
set "STARTED_SERVER=0"

call :log ""
call :log "================================================================"
call :log "  weekly doraemon sync - %DATE% %TIME%"
call :log "================================================================"

REM --- Step 1: TVer download (skips files already on disk) ---
call :log "[1/3] download_tver.py (latest episode)"
"%PY%" scripts\download_tver.py >>"%LOG%" 2>&1
if errorlevel 1 (
    set "RC=1"
    call :log "[1/3] FAILED - see log above"
    goto :cleanup
)

REM --- Step 2: fetch newly-aired episode info ---
call :log "[2/3] fetch_doraemon.py --auto"
if exist "%STATE%" del "%STATE%"
"%PY%" scripts\fetch_doraemon.py --auto >>"%LOG%" 2>&1
if errorlevel 1 (
    set "RC=1"
    call :log "[2/3] FAILED - see log above"
    goto :cleanup
)

if not exist "%STATE%" goto :no_new
for %%I in ("%STATE%") do if %%~zI==0 goto :no_new
goto :translate

:no_new
call :log "[3/3] no newly-aired episode - translation skipped"
goto :cleanup

REM --- Step 3: translate each fetched episode (needs llama server) ---
:translate
call :ensure_server
if errorlevel 1 (
    set "RC=1"
    goto :cleanup
)
for /f "usebackq" %%e in ("%STATE%") do (
    call :log "[3/3] translate_doraemon.py %%e"
    "%PY%" scripts\translate_doraemon.py %%e >>"%LOG%" 2>&1
    if errorlevel 1 (
        set "RC=1"
        call :log "[3/3] FAILED for episode %%e"
        goto :cleanup
    )
)

:cleanup
if "%STARTED_SERVER%"=="1" (
    call :log "[server] stopping translation LLM to free VRAM/RAM..."
    taskkill /IM llama-server.exe /F >nul 2>&1
)
if "%RC%"=="0" (
    call :log "DONE %TIME%"
) else (
    call :log "FAILED %TIME% - log: %LOG%"
)
exit /b %RC%


REM --- echo to console AND append to the log (echo( is empty-safe) ---
:log
echo(%~1
>>"%LOG%" echo(%~1
exit /b 0


REM --- start llama.cpp if down; wait until /health answers 200 ---
REM (copied from 02_fetch_doraemon.bat: /health is 503 while the model
REM loads, /v1/models answers 200 long before inference works)
:ensure_server
curl -sf -m 3 http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 (
    call :log "[server] translation LLM already running."
    exit /b 0
)
call :log "[server] starting translation LLM (llama.cpp)..."
start "translate-server" /min "%~dp0..\start_translate_server.bat"
set "STARTED_SERVER=1"
set /a TRIES=0
:wait_server
curl -sf -m 3 http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 (
    call :log "[server] ready."
    exit /b 0
)
set /a TRIES+=1
if %TRIES% GEQ 60 (
    call :log "[server] did not become ready in time."
    taskkill /IM llama-server.exe /F >nul 2>&1
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto :wait_server
