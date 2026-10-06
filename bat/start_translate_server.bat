@echo off
chcp 65001 >nul
rem Start the local JA->ZH translation backend (llama.cpp llama-server, Vulkan).
rem Double-click to launch; keep this window open while running translate_subtitles.bat /
rem doraemon\04_ja_asr.bat --translate / doraemon\05_learn_corrections.bat.
rem Close the window (or Ctrl+C) to stop.
rem
rem Paths (override by setting the env var before running):
if not defined LLAMA_SERVER set "LLAMA_SERVER=C:\tools\llama.cpp\llama-server.exe"
if not defined TRANSLATE_MODEL set "TRANSLATE_MODEL=C:\models\Qwen3.5-9B-Q6_K.gguf"
if not defined TRANSLATE_ALIAS set "TRANSLATE_ALIAS=qwen3.5-9b"
if not defined TRANSLATE_PORT set "TRANSLATE_PORT=8080"

echo ========================================
echo   Translation LLM server (llama.cpp)
echo ========================================
echo  model : %TRANSLATE_MODEL%
echo  alias : %TRANSLATE_ALIAS%
echo  url   : http://127.0.0.1:%TRANSLATE_PORT%/v1
echo.
echo  Serving... (this window must stay open)
echo.

"%LLAMA_SERVER%" -m "%TRANSLATE_MODEL%" --alias "%TRANSLATE_ALIAS%" ^
  --host 127.0.0.1 --port %TRANSLATE_PORT% -c 8192 -ngl 99

echo.
echo Server stopped.
pause >nul
