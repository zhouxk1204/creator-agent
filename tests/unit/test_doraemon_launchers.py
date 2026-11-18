from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_batch_video_stages_are_enabled_with_materials_only_guard():
    data = (ROOT / "bat/doraemon/最新话doraemon素材下载.bat").read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"")
    script = data.decode("utf-8").replace("\r\n", "\n")
    assert 'set "DOWNLOAD_VIDEO=1"' in script
    guard = script.index('if "%DOWNLOAD_VIDEO%"=="0" (')
    assert script.index("os.replace(temp, target)") < guard < script.index("scripts\\download_tver.py")
    assert script.index("goto :done", guard) < script.index("scripts\\download_tver.py")
    assert guard < script.index("scripts\\split_episode.py") < script.index('call "%~dp0run_upscale.bat"')


def test_ja_asr_uses_last_materials_folder_for_input_and_output():
    data = (ROOT / "bat/doraemon/04_ja_asr.bat").read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"")
    script = data.decode("ascii").replace("\r\n", "\n")

    state = 'set "MATERIALS_STATE=%REPO_ROOT%\\logs\\.last_doraemon_materials"'
    load = 'set /p "MATERIALS_DIR="<"%MATERIALS_STATE%"'
    input_default = 'if not defined HAS_INPUT_DIR set "ARGS=--input-dir "%MATERIALS_DIR%" %ARGS%"'
    output_default = 'if not defined HAS_OUT_DIR set "ARGS=%ARGS% --out-dir "%OUTDIR%""'

    assert script.index(state) < script.index(load) < script.index(input_default)
    assert script.index(input_default) < script.index(output_default)
    assert 'set "OUTDIR=%MATERIALS_DIR%"' in script
    assert "Get-Date -Format" not in script
    assert "Desktop\\ja_asr_" not in script


@pytest.mark.parametrize("download_video", [0, 1])
def test_shell_materials_only_toggle(tmp_path, download_video):
    repo = tmp_path / "repo"
    launcher = repo / "bat/doraemon/最新话doraemon素材下载.sh"
    launcher.parent.mkdir(parents=True)
    source = (ROOT / "bat/doraemon/最新话doraemon素材下载.sh").read_text(encoding="utf-8")
    launcher.write_text(source.replace("DOWNLOAD_VIDEO=0", f"DOWNLOAD_VIDEO={download_video}", 1), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [ "$*" = "run python scripts/fetch_doraemon.py --latest" ]; then\n'
        "    mkdir -p logs\n"
        '    printf "941\\n" > logs/.last_fetched\n'
        '    printf "2026-11-07\\n" > logs/.last_fetched_date\n'
        "    exit 0\n"
        "fi\n"
        "exit 42\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    opener = bin_dir / "open"
    opener.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    opener.chmod(0o755)
    home = tmp_path / "测试 home !"
    materials = home / "Desktop/doraemon_941"
    materials.mkdir(parents=True)
    existing_video = materials / "941.mp4"
    existing_video.write_bytes(b"existing video")
    calls = tmp_path / "calls.log"

    result = subprocess.run(
        ["bash", str(launcher)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "CALL_LOG": str(calls),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert (repo / "logs/.last_doraemon_materials").read_text(encoding="utf-8").strip() == str(materials)
    assert existing_video.read_bytes() == b"existing video"
    assert not list((repo / "logs").glob(".last_doraemon_materials.*"))
    commands = calls.read_text(encoding="utf-8").splitlines()
    assert commands[0] == "run python scripts/fetch_doraemon.py --latest"
    if download_video == 0:
        assert result.returncode == 0, result.stderr
        assert len(commands) == 1
        assert "Video download, split and video upscale skipped." in result.stdout
    else:
        assert result.returncode == 42
        assert len(commands) == 2
        assert "scripts/download_tver.py" in commands[1]
