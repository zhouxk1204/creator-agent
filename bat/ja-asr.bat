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

rem ---------------------------------------------------------------------------
rem Double-click (no args) = Japanese ASR -> JA .srt -> translate -> ZH .srt.
rem (No burn by default; add --burn yourself if you want a hardsubbed mp4.)
rem Pass your own args to override, e.g.:
rem   ja-asr.bat video.mp4                  (ASR only, JA .srt)
rem   ja-asr.bat video.mp4 --burn           (also burn subs into .zh.mp4)
rem ---------------------------------------------------------------------------
set "ARGS=%*"
if "%~1"=="" set "ARGS=--translate"

rem Translation needs the local LLM server (llama.cpp). Start it if it's down
rem whenever the run involves --translate / --burn (including the no-arg default).
rem If WE started it, we also stop it at the end to free VRAM/RAM; a server the
rem user started themselves (translate-server.bat) is left running.
set "NEED_SERVER=0"
set "STARTED_SERVER=0"
if "%~1"=="" set "NEED_SERVER=1"
echo %ARGS% | findstr /c:"--translate" >nul && set "NEED_SERVER=1"
echo %ARGS% | findstr /c:"--burn" >nul && set "NEED_SERVER=1"
if "%NEED_SERVER%"=="1" (
    call :ensure_server
    if errorlevel 1 (
        echo.
        echo Translation server failed to start - see above.
        pause >nul
        exit /b 1
    )
)

echo ========================================
echo   Japanese ASR (vocal separation + Qwen3-ASR)
if "%~1"=="" echo   + translate  [JA srt + ZH srt]
echo ========================================
echo.
if "%~1"=="" (
    echo  Double-click = ASR + translate every video in storage\ja_inbox\
    echo  ^(-^> ^<name^>.srt 日文 + ^<name^>.zh.srt 中文; no burn by default^).
    echo  Already-done videos are skipped automatically.
) else (
    echo  Running: ja-asr %ARGS%
)
echo.
"%PY%" -m creator_agent.cli.main ja-asr %ARGS%
set "RC=%ERRORLEVEL%"
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
curl -s -m 3 http://127.0.0.1:8080/v1/models >nul 2>&1
if not errorlevel 1 (
    echo [server] translation LLM already running.
    exit /b 0
)
echo [server] starting translation LLM ^(llama.cpp^) in a separate window...
start "translate-server" /min "%~dp0translate-server.bat"
set "STARTED_SERVER=1"
set /a TRIES=0
:wait_server
curl -s -m 3 http://127.0.0.1:8080/v1/models >nul 2>&1
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
