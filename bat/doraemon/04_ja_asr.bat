@echo off
chcp 65001 >nul
call "%~dp0..\_bootstrap.bat"
if errorlevel 1 (
    echo.
    echo Setup failed - see the message above.
    pause >nul
    exit /b 1
)
call .venv\Scripts\activate.bat >nul 2>&1

rem ---------------------------------------------------------------------------
rem 日文 ASR 管线（六步）：
rem   1. 视频提取音频      ffmpeg -> 16kHz 单声道 WAV
rem   2. 分离纯人声        BS-RoFormer（audio-separator）
rem   3. Qwen3-ASR 识别日语 按 VAD 语音块识别（Qwen3-ASR-1.7B）
rem   4. 词级时间对齐      Qwen3-ForcedAligner（独立 conda env 子进程）
rem   5. 边界检查          对照原视频+音频：ffprobe 校验时长，cue 起止向
rem                        真实静音吸附，可疑边界写入 <视频名>.check.txt
rem   6. 生成日文 SRT      <视频名>.srt（保持干净，导入剪映核对时间轴）
rem
rem 双击（无参）= 处理 ja_asr.input_dir（C:\test\temps）里的所有视频，
rem 只做 ASR + 边界检查，产出日文 SRT —— 自己导入剪映核对。
rem 传参覆盖，例如：
rem   04_ja_asr.bat video.mp4                  只处理这个视频
rem   04_ja_asr.bat video.mp4 --force          忽略已有 SRT 和音频缓存重跑
rem   04_ja_asr.bat video.mp4 --translate      ASR 后接着翻译成 .zh.srt
rem   04_ja_asr.bat video.mp4 --translate --burn   再烧录成 .zh.mp4
rem
rem 翻译需要本地 LLM server（llama.cpp），CLI 会在翻译前惰性启动、译完即停
rem —— 绝不能与 ASR 同时跑：llama-server + torch/CUDA + 人声分离 + ASR 模型
rem 一起会超 Windows commit 上限，ASR 模型加载会死于 os error 1455。
rem 自己手动起的 server（start_translate_server.bat）不会被停掉。
rem ---------------------------------------------------------------------------

echo ========================================
echo   Japanese ASR ^(vocal separation + Qwen3-ASR^)
echo   + boundary check vs original video/audio
echo ========================================
echo.
if "%~1"=="" (
    echo  Double-click = ASR + boundary check for every video in C:\test\temps\
    echo  Outputs      -^> vault episodes dir ^(ja_asr.out_dir in config\settings.yaml^)
    echo    ^<name^>.srt        Japanese subtitles ^(import into 剪映 to check the timeline^)
    echo    ^<name^>.check.txt  suspicious-boundary report
    echo  Intermediates ^(mix + vocals + instrumental^) kept in C:\test\temps\_work.
    echo  Already-done videos are skipped automatically.
    echo  ^(Add --translate yourself if you also want .zh.srt.^)
) else (
    echo  Running: ja-asr %*
)
echo.
"%PY%" -m creator_agent.cli.main ja-asr %*
set "RC=%ERRORLEVEL%"
echo.
echo ========================================
if not "%RC%"=="0" echo  FAILED ^(exit code %RC%^) - see the error above.
if "%RC%"=="0" echo  Done! Import the .srt into 剪映 to check the timeline.
echo ========================================
pause >nul
exit /b %RC%
