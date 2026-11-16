"""Repeat the stage-0 JA ASR audit without models, media, or config changes.

Run: uv run python scripts/audit_ja_asr_stage0.py
Each invocation creates a new output/ja_asr/stage0/<run-id>/ directory.
Passing characterization tests mean reproduced behavior, NOT quality acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_TESTS = [
    f"tests/unit/test_{name}.py"
    for name in (
        "audio_extractor",
        "ja_transcriber",
        "boundary_check",
        "review",
        "align_check",
        "cue_splitter",
        "subtitle",
        "build_chunks",
        "vad_chunker",
    )
]
AUDIT_TEST = "tests/unit/test_ja_asr_stage0_audit.py"
CHANGED_CODE = ["scripts/audit_ja_asr_stage0.py", AUDIT_TEST]
NOT_RUN = [
    {"test": "Windows 04_ja_asr.bat + dual conda envs", "status": "NOT RUN", "reason": "本脚本不启动实际流水线"},
    {"test": "BS-RoFormer / Silero / CAM++ / Qwen3-ASR / ForcedAligner inference", "status": "NOT RUN"},
    {"test": "真实音频听校、日文识别率、短句漏识别率、对齐误差", "status": "NOT RUN"},
    {"test": "全仓库测试（本次限定日语 ASR 相关单元测试）", "status": "NOT RUN"},
]


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(run_dir: Path, name: str, args: list[str], *, pytest_run: bool = False) -> dict:
    command = [sys.executable, "-m", *args]
    junit = run_dir / f"{name}.xml"
    if pytest_run:
        basetemp = run_dir / "raw" / name
        basetemp.parent.mkdir(parents=True, exist_ok=True)
        if basetemp.exists():
            raise FileExistsError(f"Refusing to reuse pytest basetemp: {basetemp}")
        command += [f"--junitxml={junit}", f"--basetemp={basetemp}"]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
        )
        rc, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        rc = None
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace") + "\nTIMEOUT after 300 seconds"
    (run_dir / f"{name}.log").write_text(stdout + "\n--- stderr ---\n" + stderr, encoding="utf-8")
    result = {
        "name": name,
        "command": command,
        "returncode": rc,
        "status": "PASS" if rc == 0 else ("TIMEOUT" if rc is None else "FAIL"),
        "elapsed_sec": round(time.monotonic() - started, 3),
        "log": f"{name}.log",
    }
    if junit.exists():
        suites = ET.parse(junit).getroot().findall("testsuite")
        result["counts"] = {
            key: sum(int(s.get(key, "0")) for s in suites) for key in ("tests", "failures", "errors", "skipped")
        }
        result["junit"] = junit.name
    print(f"{name}: {result['status']} {result.get('counts', '')}", flush=True)
    return result


def run_audit(output_root: Path) -> tuple[Path, dict]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    sources = sorted((ROOT / "src/creator_agent/asr").glob("*.py"))
    sources += [ROOT / p for p in (*CHANGED_CODE, "src/creator_agent/config.py", "config/settings.validate.yaml")]
    meta = {
        "run_id": run_id,
        "stage": 0,
        "started_at": datetime.now(UTC).isoformat(),
        "git_revision": revision,
        "python": sys.version,
        "platform": platform.platform(),
        "torch_installed": importlib.util.find_spec("torch") is not None,
        "models_executed": False,
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }
    _write_json(run_dir / "manifest.json", meta)
    runs = [
        _run(run_dir, "baseline", ["pytest", *BASELINE_TESTS, "-q"], pytest_run=True),
        _run(run_dir, "characterization", ["pytest", AUDIT_TEST, "-q"], pytest_run=True),
        _run(run_dir, "ruff_check", ["ruff", "check", *CHANGED_CODE]),
        _run(run_dir, "ruff_format", ["ruff", "format", "--check", *CHANGED_CODE]),
    ]
    cases = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted((run_dir / "raw/characterization").rglob("case.json"))
    ]
    result = {
        **meta,
        "finished_at": datetime.now(UTC).isoformat(),
        "runs": runs,
        "cases": cases,
        "not_run": NOT_RUN,
        "quality_acceptance": "NOT RUN",
        "next_stage": "BLOCKED_PENDING_USER_CONFIRMATION",
        "interpretation": (
            "characterization PASS 只表示问题复现成功；各 case 的 requirement_status 才表示需求是否满足。"
        ),
    }
    _write_json(run_dir / "results.json", result)
    changes = """# 阶段 0 代码变更记录

- 新增 `scripts/audit_ja_asr_stage0.py`：仅运行离线审计测试。
  保留独立运行编号、源文件哈希、原始输入输出、日志和 JUnit/JSON。
- 新增 `tests/unit/test_ja_asr_stage0_audit.py`：固定当前问题的纯函数/模拟复现。PASS 不等于字幕质量合格。
- 未修改 `src/`、bat、模型、ForcedAligner、配置参数、缓存、断点续跑或恢复逻辑。
- 修改前后：生产字幕行为相同；新增可复现证据。后续修复对应阶段时，应把问题刻画测试转为修复回归测试。
- 本运行目录仅含合成输入和测试产物，无用户视频、真实音频或模型输出。
"""
    (run_dir / "changes.md").write_text(changes, encoding="utf-8")
    lines = [
        "# 日语 ASR 阶段 0 — 自动审计复现报告",
        "",
        f"运行编号：`{run_id}`；代码基准：`{revision}`。",
        "",
        "## 目标与范围",
        "仅复现当前代码问题、建立离线基线；不修复生产算法，不进入阶段 1，不执行翻译。",
        "这是代码/合成数据证据，不能替代真实日语音频验收。源文件版本见 manifest.json。",
        "",
        "## 检查函数、问题证据与未解决事项",
    ]
    for case in cases:
        lines += [
            "",
            f"### {case['case_id']} — {case['requirement_status']}",
            case["requirement"],
            "",
            *[f"- `{e}`" for e in case["evidence"]],
            "",
            "输入与实际输出（完整原始数据另存 results.json 和 raw/）：",
            "```json",
            json.dumps({"inputs": case["inputs"], "observed": case["observed"]}, ensure_ascii=False, indent=2),
            "```",
        ]
    lines += ["", "## 自动测试结果", "", result["interpretation"], ""]
    for run in runs:
        lines.append(
            f"- {run['name']}: **{run['status']}**，exit={run['returncode']}，"
            f"{run.get('counts', {})}；详见 {run['log']}。"
        )
    lines += ["", "## 未运行的验证", "", *[f"- {t['test']}: **NOT RUN**。" for t in NOT_RUN]]
    lines += [
        "",
        "## 修改内容及前后区别",
        changes.split("\n", 1)[1],
        "## 用户本地验证与停止点",
        "1. 在项目根目录运行 `uv run python scripts/audit_ja_asr_stage0.py`，每次自动新建运行目录，不覆盖旧结果。",
        "2. 核对 baseline.log 中已有失败与 characterization.log 的问题复现；非零退出不能当作 PASS。",
        "3. 如需跑真实视频，使用现有 04_ja_asr.bat，指定全新 --out-dir；不要传 --translate / --burn。",
        "4. 保留原始视频、人声音频、SRT、TXT、transcript、review、check、dropped 与终端日志，人工核对目标短句及边界。",
        "5. 此阶段未修复字幕质量；完成本地核对后由用户确认，才开始阶段 1。",
    ]
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Stage 0 outputs: {run_dir}", flush=True)
    return run_dir, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "output/ja_asr/stage0")
    args = parser.parse_args()
    _, result = run_audit(args.output_root)
    return int(any(run["status"] != "PASS" for run in result["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
